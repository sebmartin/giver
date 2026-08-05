import asyncio
import logging


async def spawn(
    cmd: list[str], prompt: str, log: logging.Logger
) -> tuple[asyncio.subprocess.Process, asyncio.Task[None]]:
    """Start a harness process with the prompt on stdin.

    Returns the process and the stderr-drain task. The caller must await that
    task before returning: the event loop holds only a weak reference to a
    running task, so an unreferenced one can be collected mid-drain, and a
    child whose stderr nobody reads blocks on the write while the parent is
    still waiting on stdout — neither side moves again.

    Closing stdin is not optional either: a harness reading its prompt from `-`
    blocks until EOF.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    drain = asyncio.create_task(_drain_stderr(proc, log))
    assert proc.stdin is not None
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()
    return proc, drain


async def _drain_stderr(proc: asyncio.subprocess.Process, log: logging.Logger) -> None:
    assert proc.stderr is not None
    async for line in proc.stderr:
        log.debug(line.decode().rstrip())
