from typing import Annotated, Protocol

from pydantic import Field

from giver.kernel.nodes.agent import AgentNode
from giver.kernel.nodes.bash import BashNode


class Node(Protocol):
    name: str
    depends_on: list[str]

    def should_skip(self) -> bool: ...
    async def run(self) -> int: ...


NodeField = Annotated[BashNode | AgentNode, Field(discriminator="type")]
