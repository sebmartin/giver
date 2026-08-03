import asyncio
import logging


async def drain_stderr(proc: asyncio.subprocess.Process, log: logging.Logger) -> None:
    """Read stderr concurrently with stdout.

    A child that fills the stderr pipe blocks on the write while the parent is
    still waiting on stdout, and neither side moves again.
    """
    assert proc.stderr is not None
    async for line in proc.stderr:
        log.debug(line.decode().rstrip())
