import asyncio
import json
import logging

from giver.harness import AgentStep, drain_stderr


class ClaudeHarness:
    """Anthropic's own tooling, CLI form.

    Same one-shot-per-step shape as pi, with a different flag dialect:
    `--resume <id> --fork-session`. `--fork-session` is opt-in — a bare
    `--resume` continues in place and *mutates* the parent session, which would
    corrupt the branch a checkpointed replay resumes from.

    `bypassPermissions` is acceptable because the container is the isolation
    boundary — that is what converts "the agent must ask" into "the agent
    can't reach".
    """

    name = "claude"
    state_path = "~/.claude"
    # Empty until an interactive `giver shell claude` login is observed. Unlike
    # pi, nothing in claude's surface suggests a local OAuth callback server —
    # `claude setup-token` mints a credential without one. Do not invent values.
    env: dict[str, str] = {}
    ports: tuple[str, ...] = ()
    repl_cmd = ("claude",)
    install = "npm install -g @anthropic-ai/claude-code"

    _VENDORS = frozenset({"anthropic"})

    # `--resume <id> --fork-session` branches; the flag is opt-in.
    forks_on_resume = True

    def serves(self, vendor: str) -> bool:
        return vendor in self._VENDORS

    async def run(self, steps: list[AgentStep], log: logging.Logger) -> int:
        session_id: str | None = None
        for index, step in enumerate(steps):
            assert step.model is not None  # resolved at load time
            cmd = [
                "claude",
                "--print",
                "--output-format", "stream-json",
                "--verbose",
                "--permission-mode", "bypassPermissions",
                "--model", step.model.split("/", 1)[1],  # claude wants the bare name
            ]
            if session_id:
                cmd += ["--resume", session_id, "--fork-session"]

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

            ok, session_id = await self._consume(proc, log, session_id)
            exit_code = await proc.wait()

            if exit_code != 0:
                log.error(f"claude exited {exit_code}")
                return 1
            if not ok:
                return 1
            if session_id is None and index < len(steps) - 1:
                log.error("claude reported no session id; the next step cannot resume")
                return 1
        return 0

    async def _consume(
        self, proc: asyncio.subprocess.Process, log: logging.Logger, session_id: str | None
    ) -> tuple[bool, str | None]:
        assert proc.stdout is not None
        success = False
        async for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                log.debug(f"unparsed stdout line: {raw!r}")
                continue
            if sid := event.get("session_id"):
                session_id = sid
            event_type = event.get("type")
            if event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        log.info(block["text"])
            elif event_type == "result":
                success = event.get("subtype") == "success" and not event.get("is_error", False)
            else:
                log.debug(f"unhandled event: {raw!r}")
        return success, session_id
