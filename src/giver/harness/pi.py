import asyncio
import json
import logging

from giver.harness.process import spawn
from giver.harness.protocol import AgentStep


class PiHarness:
    """Model-agnostic workhorse: every vendor by API key.

    Driven as a stateless one-shot per step, threading continuity through
    `--fork <session>`. Forking rather than continuing in place leaves the
    parent session untouched, so replaying a step from a checkpoint is
    idempotent. Per-step model switching falls out for free — each step spawns
    its own process.
    """

    name = "pi"
    state_path = "~/.pi/agent"
    env = {"PI_OAUTH_CALLBACK_HOST": "0.0.0.0"}
    ports = ("53692:53692",)
    repl_cmd = ("pi",)
    install = "npm install -g --ignore-scripts @earendil-works/pi-coding-agent"

    # `--fork <id>` branches rather than continuing in place.
    forks_on_resume = True

    def serves(self, vendor: str) -> bool:
        return True  # the general-purpose harness; anything unclaimed lands here

    async def run(self, steps: list[AgentStep], log: logging.Logger) -> int:
        session_id: str | None = None
        for index, step in enumerate(steps):
            assert step.model is not None  # resolved at load time
            cmd = ["pi", "--mode", "json", "--print", "--model", step.model]
            if session_id:
                cmd += ["--fork", session_id]

            proc, drain = await spawn(cmd, step.prompt, log)
            ok, session_id = await self._consume(proc, log, session_id)
            exit_code = await proc.wait()
            await drain

            # Two independent failure channels: a non-zero exit, and an in-band
            # failure reported on a stream that then exited cleanly.
            if exit_code != 0:
                log.error(f"pi exited {exit_code}")
                return 1
            if not ok:
                return 1
            if session_id is None and index < len(steps) - 1:
                log.error("pi reported no session id; the next step cannot fork")
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
            event_type = event.get("type")
            if event_type == "session":
                # Documented shape: the first line of `--mode json` stdout is
                # {"type":"session","version":3,"id":...,"cwd":...}
                session_id = event.get("id") or session_id
            elif event_type == "message_update":
                delta = event.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta":
                    log.info(delta["delta"])
            elif event_type == "agent_end":
                success = not event.get("willRetry", False)
            else:
                log.debug(f"unhandled event: {raw!r}")
        return success, session_id
