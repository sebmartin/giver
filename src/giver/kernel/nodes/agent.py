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
    async def run(
        self, steps: list[AgentStep], default_model: str | None, log: logging.Logger
    ) -> int:
        cmd = ["pi", "--mode", "rpc"]
        if default_model:
            cmd += ["--model", default_model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(_drain_stderr(proc, log))

        current_model = default_model
        for step in steps:
            step_model = step.model or default_model
            if step_model and step_model != current_model:
                await _send(proc, {
                    "type": "set_model",
                    "provider": _infer_provider(step_model),
                    "modelId": step_model,
                })
                current_model = step_model
            await _send(proc, {"type": "prompt", "message": step.prompt})
            if not await _wait_for_agent_end(proc, log):
                proc.stdin.close()
                await proc.wait()
                return 1

        proc.stdin.close()
        return await proc.wait()


class ClaudeRunner:
    async def run(
        self, steps: list[AgentStep], default_model: str | None, log: logging.Logger
    ) -> int:
        from claude_agent_sdk import (  # type: ignore[import]
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        session_id: str | None = None
        for step in steps:
            options = ClaudeAgentOptions(
                model=step.model or default_model,
                resume=session_id,
                permission_mode="bypassPermissions",
            )
            async for message in query(step.prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            log.info(block.text)
                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    if message.subtype != "success":
                        return 1
        return 0


_RUNNER_FOR_PROVIDER: dict[str | None, type] = {
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

    @model_validator(mode="after")
    def _validate_single_runner(self) -> "AgentNode":
        runners = {
            _RUNNER_FOR_PROVIDER.get(_infer_provider(step.model or self.model), PiRunner)
            for step in self.steps
        }
        if len(runners) > 1:
            names = ", ".join(r.__name__ for r in sorted(runners, key=lambda r: r.__name__))
            raise ValueError(
                f"Node {self.name!r} mixes runners ({names}). "
                "Use separate nodes for cross-vendor workflows."
            )
        return self

    def should_skip(self) -> bool:
        return self.output is not None and Path(self.output).exists()

    async def run(self) -> int:
        log = logging.getLogger(self.name)
        providers = {_infer_provider(step.model or self.model) for step in self.steps}
        runner_cls = _RUNNER_FOR_PROVIDER.get(next(iter(providers)), PiRunner)
        return await runner_cls().run(self.steps, self.model, log)


async def _send(proc: asyncio.subprocess.Process, cmd: dict) -> None:
    proc.stdin.write((json.dumps(cmd) + "\n").encode())
    await proc.stdin.drain()


async def _wait_for_agent_end(proc: asyncio.subprocess.Process, log: logging.Logger) -> bool:
    async for raw in proc.stdout:
        event = json.loads(raw)
        if event.get("type") == "message_update":
            delta = event.get("assistantMessageEvent", {})
            if delta.get("type") == "text_delta":
                log.info(delta["delta"])
        elif event.get("type") == "agent_end":
            return not event.get("willRetry", False)
    return False


async def _drain_stderr(proc: asyncio.subprocess.Process, log: logging.Logger) -> None:
    async for line in proc.stderr:
        log.debug(line.decode().rstrip())
