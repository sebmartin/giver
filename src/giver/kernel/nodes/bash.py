import asyncio
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class BashNode(BaseModel):
    type: Literal["bash"]
    name: str
    command: str
    depends_on: list[str] = []
    output: str | None = None

    def should_skip(self) -> bool:
        return self.output is not None and Path(self.output).exists()

    async def run(self) -> int:
        log = logging.getLogger(self.name)
        proc = await asyncio.create_subprocess_shell(
            self.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            log.info(line.decode().rstrip())
        return await proc.wait()
