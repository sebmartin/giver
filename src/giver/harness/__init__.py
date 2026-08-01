"""Harnesses: the coding-agent programs give'r drives.

One description per harness, read by both layers — the host CLI provisions a
runtime from the declared infrastructure, the kernel invokes behaviour. A
harness states `~/.pi/agent`, never `/root/...`; expanding that into a container
path is the consumer's job.

`run` is a contract — "execute these steps, stream to the log, return status" —
not "spawn this command". Both harnesses shipped here shell out, but an
in-process one stays buildable.
"""

import asyncio
import logging
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class Vendor(BaseModel):
    """A credential namespace and routing pivot. A local ollama endpoint is a
    vendor too, possibly credential-less."""

    name: str


class AgentStep(BaseModel):
    # extra="forbid" so a `harness:` key on a step is a load error rather than a
    # silently dropped field — a harness cannot vary per step (the steps of one
    # node share a session, and session ids belong to the harness that issued
    # them).
    model_config = ConfigDict(extra="forbid")

    prompt: str
    model: str | None = None


class Harness(Protocol):
    name: str
    state_path: str  # where it keeps credentials and sessions; `~` stays
    env: dict[str, str]
    ports: tuple[str, ...]
    repl_cmd: tuple[str, ...]  # interactive launch argv — data, not a method
    install: str  # shell command that puts it in an image; one generated RUN line

    def serves(self, vendor: str) -> bool: ...

    async def run(self, steps: list[AgentStep], log: logging.Logger) -> int: ...


# Brand prefixes, not model names: vendors ship new models far more often than
# they ship a new brand. A name no prefix claims is ambiguous on purpose —
# `qwen-2.5-coder` is served by ollama, together and fireworks with different
# credentials and, locally, no cost at all.
VENDOR_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
}


def resolve_model(value: str) -> str:
    """Canonicalize a model to `vendor/model`.

    A qualified value passes through unvalidated — vendors add models faster
    than give'r updates, and the harness itself rejects a bad id with a real
    error. A bare name resolves by brand prefix, or raises.
    """
    if "/" in value:
        return value
    for prefix, vendor in VENDOR_PREFIXES.items():
        if value.startswith(prefix):
            return f"{vendor}/{value}"
    raise ValueError(
        f"cannot infer a vendor for model {value!r} — write it as vendor/{value}"
    )


def vendor_of(model: str) -> str:
    return model.split("/", 1)[0]


def harness_by_name(name: str | None) -> Harness:
    """The harness a workflow named, or the general-purpose one."""
    if name is None:
        return DEFAULT_HARNESS
    for harness in HARNESSES:
        if harness.name == name:
            return harness
    known = ", ".join(h.name for h in HARNESSES)
    raise ValueError(f"unknown harness {name!r}. choices: {known}")


async def drain_stderr(proc: asyncio.subprocess.Process, log: logging.Logger) -> None:
    """Read stderr concurrently with stdout, or a child that fills the stderr
    pipe blocks forever while the parent waits on stdout."""
    assert proc.stderr is not None
    async for line in proc.stderr:
        log.debug(line.decode().rstrip())


from giver.harness.claude import ClaudeHarness  # noqa: E402
from giver.harness.pi import PiHarness  # noqa: E402

HARNESSES: tuple[Harness, ...] = (ClaudeHarness(), PiHarness())
DEFAULT_HARNESS: Harness = HARNESSES[-1]
