from typing import Annotated, Protocol

from pydantic import Field

from giver.kernel.nodes.bash import BashNode


class Node(Protocol):
    name: str

    async def run(self) -> int: ...


NodeField = Annotated[BashNode, Field(discriminator="type")]
