import shlex
from typing import Literal

from pydantic import model_validator

from giver.kernel.nodes.bash import BashNode


class AgentNode(BashNode):
    type: Literal["agent"]
    prompt: str
    model: str | None = None
    command: str = ""  # computed from prompt; not user-supplied

    @model_validator(mode="after")
    def _compile(self) -> "AgentNode":
        model_flag = f"--model {shlex.quote(self.model)} " if self.model else ""
        self.command = f"pi --mode json {model_flag}-p {shlex.quote(self.prompt)}"
        return self
