import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path

from giver.harness.process import spawn
from giver.harness.protocol import AgentStep


class ClaudeCodeHarness:
    """Anthropic's own tooling, CLI form.

    Same one-shot-per-step shape as pi, with a different flag dialect:
    `--resume <id> --fork-session`. `--fork-session` is opt-in — a bare
    `--resume` continues in place and *mutates* the parent session, which would
    corrupt the branch a checkpointed replay resumes from.

    `bypassPermissions` is acceptable because the container is the isolation
    boundary — that is what converts "the agent must ask" into "the agent
    can't reach".
    """

    name = "claude-code"
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

    def prepare(self) -> None:
        """Bring `~/.claude.json` inside the state directory.

        claude splits its state in two: sessions and credentials live under
        `~/.claude`, but the config file is `~/.claude.json` — a *sibling* of
        that directory, not a file in it. Anything that persists `state_path`
        alone therefore keeps the credentials and loses the config, and the
        config is what carries `hasCompletedOnboarding` and the `oauthAccount`
        identity — so the agent reads as logged out and re-runs onboarding even
        though its token is right there. Linking the config into the state
        directory means whatever backs `~/.claude` backs both.

        Careful about what it displaces, because give'r is meant to run without
        a container too, where `~/.claude.json` is someone's real config: an
        existing file is moved in rather than dropped, and if both sides hold
        one this leaves them alone rather than picking a winner.
        """
        link = Path("~/.claude.json").expanduser()
        target = Path("~/.claude/claude.json").expanduser()
        if link.is_symlink():
            return
        if link.exists():
            if target.exists():
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            link.rename(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        with suppress(FileExistsError):  # parallel nodes prepare concurrently
            link.symlink_to(target)

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

            proc, drain = await spawn(cmd, step.prompt, log)
            ok, session_id = await self._consume(proc, log, session_id)
            exit_code = await proc.wait()
            await drain

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
