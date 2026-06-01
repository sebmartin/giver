import asyncio
import logging
from typing import Literal

from pydantic import BaseModel


class BashNode(BaseModel):
    type: Literal["bash"]
    name: str
    command: str

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
