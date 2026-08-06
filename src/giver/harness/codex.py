import asyncio
import json
import logging

from giver.harness.process import spawn
from giver.harness.protocol import AgentStep


class CodexHarness:
    """OpenAI's CLI.

    Shaped differently from the other two in ways worth noting, because they are
    what the Protocol has to absorb without special-casing:

    - Headless is a *subcommand* (`codex exec`), not a flag.
    - Continuity is `codex exec resume <id>`, which continues a session **in
      place**. There is no forking variant in headless mode, so `forks_on_resume`
      is False and replaying a step is not idempotent here.
    - It calls sessions *threads*: the id arrives as `thread_id` on a
      `thread.started` event. Resuming re-emits the *same* id, which is what
      "in place" looks like on the wire.
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

    def prepare(self) -> None:
        """Nothing to arrange: `auth.json`, `config.toml` and sessions all sit
        under `state_path`."""

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

            proc, drain = await spawn(cmd, step.prompt, log)
            ok, session_id = await self._consume(proc, log, session_id)
            exit_code = await proc.wait()
            await drain

            if exit_code != 0:
                log.error(f"codex exited {exit_code}")
                return 1
            if not ok:
                return 1
            if session_id is None and index < len(steps) - 1:
                log.error("codex reported no thread id; the next step cannot resume")
                return 1
        return 0

    async def _consume(
        self, proc: asyncio.subprocess.Process, log: logging.Logger, session_id: str | None
    ) -> tuple[bool, str | None]:
        """Returns (ok, session_id).

        Success needs a positive `turn.completed`, the same polarity as pi's
        `agent_end` and claude-code's `result`. Absence of evidence is not
        success: a truncated stream and a terminal event under a name this code
        does not know both land here, and give'r skips a node whose output
        artifact exists — so a run wrongly recorded as successful can never be
        regenerated.

        The verdict comes only from turn-level events. An `error` item inside
        `item.completed` is advisory — codex emits one for a soft condition
        like unrecognised model metadata, then carries on — so it is logged and
        left to `turn.completed`/`turn.failed` to adjudicate.

        Event shapes verified against codex-cli 0.146.0.
        """
        assert proc.stdout is not None
        success = False
        failed = False
        async for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                # Defensive: every observed stdout line is JSONL, but a stray
                # plain-text line must not abort the run.
                log.debug(f"unparsed stdout line: {raw!r}")
                continue
            event_type = event.get("type")
            if event_type == "thread.started":
                session_id = event.get("thread_id") or session_id
            elif event_type == "turn.completed":
                success = True
            elif event_type in ("error", "turn.failed"):
                failed = True
                log.error(self._message(event) or raw)
            elif event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "error":
                    log.error(self._message(item) or raw)
                elif text := item.get("text"):
                    log.info(text)
            else:
                log.debug(f"unhandled event: {raw!r}")
        if not success and not failed:
            log.error("codex stream ended without a turn.completed event")
        return success and not failed, session_id

    @staticmethod
    def _message(event: dict) -> str | None:
        """codex reports a failure's text either flat or nested under `error`.

        `error` and item errors carry `message`; `turn.failed` nests it under
        `error`. Tolerant of the shape because the caller falls back to the raw
        line: a failure must always be legible in the log, whatever carried it.
        """
        nested = event.get("error")
        if isinstance(nested, dict):
            nested = nested.get("message")
        return event.get("message") or nested
