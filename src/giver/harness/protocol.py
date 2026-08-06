import logging
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class AgentStep(BaseModel):
    # extra="forbid" so a `harness:` key on a step is a load error rather than a
    # silently dropped field — a harness cannot vary per step, because the steps
    # of one node share a session and session ids belong to the harness that
    # issued them.
    model_config = ConfigDict(extra="forbid")

    prompt: str
    model: str | None = None


class Harness(Protocol):
    """What give'r needs to know about one coding-agent CLI.

    The host CLI reads the declared fields to build a container for it; the
    kernel calls `run` to do the work. Paths are given the way the harness
    itself writes them — pi says `~/.pi/agent` — and the host decides where that
    ends up in a container.

    `run` takes a node's steps, streams output to the log, and returns an exit
    status. How it gets there is up to the harness: pi and claude-code each
    spawn a process per step, and a harness that called a library instead would
    satisfy the same contract.
    """

    name: str
    state_path: str  # where it keeps credentials and sessions
    env: dict[str, str]  # environment it needs, whenever it runs
    ports: tuple[str, ...]  # ports its interactive login needs published
    repl_cmd: tuple[str, ...]  # interactive launch argv
    install: str  # shell command that installs it; one generated RUN line

    # Can it branch a session instead of continuing it in place? Branching
    # leaves the parent untouched, which is what makes replaying a step safe.
    # pi and claude-code can; codex cannot. Read it rather than assume it.
    forks_on_resume: bool

    def serves(self, vendor: str) -> bool: ...

    def prepare(self) -> None:
        """Arrange whatever this harness needs before it is used, in place.

        Called in the environment the harness will run in, once per entry
        point: the CLI's shim runs it before handing a container to `shell` or
        `chat`, and an agent node runs it before its steps. give'r decides where
        a harness's `state_path` lands; `prepare` is where the harness reconciles
        that with anything it keeps elsewhere. Must be idempotent — parallel
        nodes call it concurrently — and must not destroy state it did not
        write, because the same code runs against a real home directory.
        """

    async def run(self, steps: list[AgentStep], log: logging.Logger) -> int: ...
