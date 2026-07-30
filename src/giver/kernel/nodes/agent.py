import asyncio
import json
import logging
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, model_validator


class AgentStep(BaseModel):
    prompt: str
    model: str | None = None


class AgentRunner(Protocol):
    async def run(
        self, steps: list[AgentStep], default_model: str | None, log: logging.Logger
    ) -> int: ...


class PiRunner:
    """Drives pi as a stateless one-shot per step, threading continuity through
    `--fork <session>`. Forking (rather than continuing in place) leaves the parent
    session untouched, so replaying a step from a checkpoint is idempotent.
    Per-step model switching falls out for free: each step spawns its own process.
    """

    async def run(
        self, steps: list[AgentStep], default_model: str | None, log: logging.Logger
    ) -> int:
        session_id: str | None = None
        for step in steps:
            cmd = ["pi", "--mode", "json", "--print"]
            model = step.model or default_model
            if model:
                cmd += ["--model", model]
            if session_id:
                cmd += ["--fork", session_id]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            asyncio.create_task(_drain_stderr(proc, log))
            proc.stdin.write(step.prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            ok, session_id = await _consume_pi_stream(proc, log, session_id)
            await proc.wait()
            if not ok:
                return 1
        return 0


class ClaudeRunner:
    async def run(
        self, steps: list[AgentStep], default_model: str | None, log: logging.Logger
    ) -> int:
        session_id: str | None = None
        for step in steps:
            cmd = [
                "claude", "--print",
                "--output-format", "stream-json",
                "--verbose",
                "--permission-mode", "bypassPermissions",
            ]
            model = step.model or default_model
            if model:
                cmd += ["--model", model]
            if session_id:
                # --fork-session is opt-in: without it --resume continues in place and
                # mutates the parent session, so replaying a checkpointed step would
                # corrupt the branch being resumed from.
                cmd += ["--resume", session_id, "--fork-session"]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            asyncio.create_task(_drain_stderr(proc, log))
            proc.stdin.write(step.prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            ok, session_id = await _consume_claude_stream(proc, log, session_id)
            await proc.wait()
            if not ok:
                return 1
        return 0


_RUNNER_FOR_PROVIDER: dict[str, type[AgentRunner]] = {
    "anthropic": ClaudeRunner,
    "openai": PiRunner,
    "local": PiRunner,
}


def _infer_provider(model: str | None) -> str | None:
    if model is None:
        return None
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    return None


class AgentNode(BaseModel):
    type: Literal["agent"]
    name: str
    depends_on: list[str] = []
    model: str | None = None
    output: str | None = None
    steps: list[AgentStep]

    def _runners(self) -> set[type[AgentRunner]]:
        return {
            _RUNNER_FOR_PROVIDER.get(_infer_provider(step.model or self.model), PiRunner)
            for step in self.steps
        }

    @model_validator(mode="after")
    def _validate_single_runner(self) -> "AgentNode":
        runners = self._runners()
        if len(runners) > 1:
            names = ", ".join(sorted(r.__name__ for r in runners))
            raise ValueError(
                f"Node {self.name!r} mixes runners ({names}). "
                "Use separate nodes for cross-vendor workflows."
            )
        return self

    def should_skip(self) -> bool:
        return self.output is not None and Path(self.output).exists()

    async def run(self) -> int:
        log = logging.getLogger(self.name)
        runner_cls = next(iter(self._runners()))
        return await runner_cls().run(self.steps, self.model, log)


async def _consume_pi_stream(
    proc: asyncio.subprocess.Process, log: logging.Logger, session_id: str | None
) -> tuple[bool, str | None]:
    success = False
    async for raw in proc.stdout:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "session":
            session_id = event.get("sessionId") or event.get("id") or session_id
        elif event.get("type") == "message_update":
            delta = event.get("assistantMessageEvent", {})
            if delta.get("type") == "text_delta":
                log.info(delta["delta"])
        elif event.get("type") == "agent_end":
            success = not event.get("willRetry", False)
    return success, session_id


async def _consume_claude_stream(
    proc: asyncio.subprocess.Process, log: logging.Logger, session_id: str | None
) -> tuple[bool, str | None]:
    success = False
    async for raw in proc.stdout:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if sid := event.get("session_id"):
            session_id = sid
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    log.info(block["text"])
        elif event.get("type") == "result":
            success = event.get("subtype") == "success" and not event.get("is_error", False)
    return success, session_id


async def _drain_stderr(proc: asyncio.subprocess.Process, log: logging.Logger) -> None:
    async for line in proc.stderr:
        log.debug(line.decode().rstrip())
