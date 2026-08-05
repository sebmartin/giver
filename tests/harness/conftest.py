import json
from unittest.mock import AsyncMock, MagicMock

import pytest


class _AsyncLines:
    """Async iterator over pre-encoded stdout lines.

    A dict is serialised as JSONL; a `str` passes through verbatim, so a test
    can feed the plain-text lines a real harness interleaves with its JSON.
    """

    def __init__(self, events: list[dict | str]):
        self._lines = iter(
            ((e if isinstance(e, str) else json.dumps(e)) + "\n").encode() for e in events
        )

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


@pytest.fixture
def mock_proc():
    """Build a stand-in for an asyncio subprocess.

    `events` is what the harness reads off stdout — dicts become JSONL, strings
    are written verbatim; `exit_code` is the process's own, independent failure
    channel.
    """

    def _make(events: list[dict | str], exit_code: int = 0) -> MagicMock:
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdin.close = MagicMock()
        proc.stdout = _AsyncLines(events)
        proc.stderr = _AsyncLines([])
        proc.wait = AsyncMock(return_value=exit_code)
        return proc

    return _make
