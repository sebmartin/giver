import asyncio
import json
import logging

from giver.harness import AgentStep, drain_stderr


class CodexHarness:
    """OpenAI's CLI.

    Shaped differently from the other two in ways worth noting, because they are
    what the Protocol has to absorb without special-casing:

    - Headless is a *subcommand* (`codex exec`), not a flag.
    - Continuity is `codex exec resume <id>`, which continues a session **in
      place**. There is no forking variant in headless mode, so `forks_on_resume`
      is False and replaying a step is not idempotent here.
    - It calls sessions *threads*: the id arrives as `thread_id` on a
      `thread.started` event.
    """

    name = "codex"
    state_path = "~/.codex"
    # Unset until an interactive `giver shell codex` login is observed.
    env: dict[str, str] = {}
    ports: tuple[str, ...] = ()
    repl_cmd = ("codex",)
    install = "npm install -g @openai/codex"

    # Resuming continues in place rather than branching, so a replayed step
    # mutates the session it resumed from. Anything that replays work has to
    # account for that instead of assuming every harness can fork.
    forks_on_resume = False

    _VENDORS = frozenset({"openai"})

    def serves(self, vendor: str) -> bool:
        return vendor in self._VENDORS

    async def run(self, steps: list[AgentStep], log: logging.Logger) -> int:
        session_id: str | None = None
        for index, step in enumerate(steps):
            assert step.model is not None  # resolved at load time
            cmd = ["codex", "exec"]
            if session_id:
                cmd += ["resume"]
            cmd += [
                "--json",
                "--model", step.model.split("/", 1)[1],  # codex wants the bare name
                "--dangerously-bypass-approvals-and-sandbox",
            ]
            if session_id:
                cmd += [session_id]
            cmd += ["-"]  # prompt on stdin

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            asyncio.create_task(drain_stderr(proc, log))
            proc.stdin.write(step.prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            failed, session_id = await self._consume(proc, log, session_id)
            exit_code = await proc.wait()

            if exit_code != 0:
                log.error(f"codex exited {exit_code}")
                return 1
            if failed:
                return 1
            if session_id is None and index < len(steps) - 1:
                log.error("codex reported no thread id; the next step cannot resume")
                return 1
        return 0

    async def _consume(
        self, proc: asyncio.subprocess.Process, log: logging.Logger, session_id: str | None
    ) -> tuple[bool, str | None]:
        """Returns (failed, session_id).

        Success is the absence of an in-band `error` event on a stream that then
        exits cleanly, rather than a positive completion event — the error and
        `thread.started` shapes are observed, a completion event has not been.
        Both failure channels still get read, which is what matters.
        """
        assert proc.stdout is not None
        failed = False
        async for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                # codex interleaves plain-text log lines with its JSONL.
                log.debug(f"unparsed stdout line: {raw!r}")
                continue
            event_type = event.get("type")
            if event_type == "thread.started":
                session_id = event.get("thread_id") or session_id
            elif event_type == "error":
                failed = True
                log.error(event.get("message", raw))
            elif event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "error":
                    failed = True
                    log.error(item.get("message", raw))
                elif text := item.get("text"):
                    log.info(text)
            else:
                log.debug(f"unhandled event: {raw!r}")
        return failed, session_id
