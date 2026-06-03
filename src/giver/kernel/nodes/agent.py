import shlex
from typing import Literal

from pydantic import model_validator

from giver.kernel.nodes.bash import BashNode


class AgentNode(BashNode):
    type: Literal["agent"]
    prompt: str
    command: str = ""  # computed from prompt; not user-supplied

    @model_validator(mode="after")
    def _compile(self) -> "AgentNode":
        self.command = f"pi --mode json -p {shlex.quote(self.prompt)}"
        return self
