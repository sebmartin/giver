import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class AgentStep(BaseModel):
    prompt: str
    model: str | None = None


class AgentNode(BaseModel):
    type: Literal["agent"]
    name: str
    depends_on: list[str] = []
    model: str | None = None
    output: str | None = None
    steps: list[AgentStep]

    def should_skip(self) -> bool:
        return self.output is not None and Path(self.output).exists()

    async def run(self) -> int:
        log = logging.getLogger(self.name)
        cmd = ["pi", "--mode", "rpc"]
        if self.model:
            cmd += ["--model", self.model]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(_drain_stderr(proc, log))

        current_model = self.model
        for step in self.steps:
            step_model = step.model or self.model
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


def _infer_provider(model: str) -> str:
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    return "anthropic"
