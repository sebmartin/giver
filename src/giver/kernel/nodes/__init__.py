from typing import Annotated, Protocol

from pydantic import Field

from giver.kernel.nodes.bash import BashNode


class Node(Protocol):
    name: str
    depends_on: list[str]

    async def run(self) -> int: ...


NodeField = Annotated[BashNode, Field(discriminator="type")]
