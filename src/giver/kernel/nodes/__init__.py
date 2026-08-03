from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import Field

from giver.kernel.nodes.agent import AgentNode
from giver.kernel.nodes.bash import BashNode

if TYPE_CHECKING:
    from giver.kernel.workflow import Defaults


class Node(Protocol):
    name: str
    depends_on: list[str]

    def apply_defaults(self, defaults: "Defaults") -> None: ...
    def harness_name(self) -> str | None: ...
    def should_skip(self) -> bool: ...
    async def run(self) -> int: ...


NodeField = Annotated[BashNode | AgentNode, Field(discriminator="type")]
