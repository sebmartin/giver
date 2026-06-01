import logging
import time
from pathlib import Path

import yaml
from pydantic import BaseModel

from giver.kernel.logging import Logger
from giver.kernel.nodes import Node, NodeField


class Workflow(BaseModel):
    name: str
    nodes: list[NodeField]

    @classmethod
    def from_file(cls, path: Path) -> "Workflow":
        return cls.model_validate(yaml.safe_load(path.read_text()))

    async def run(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        with Logger(log_dir, [n.name for n in self.nodes]):
            for node in self.nodes:
                await self._run_node(node)

    async def _run_node(self, node: Node) -> int:
        log = logging.getLogger(node.name)
        log.info(f"starting {node.name!r}")
        started = time.monotonic()
        exit_code = await node.run()
        elapsed = time.monotonic() - started
        if exit_code == 0:
            log.info(f"{node.name!r} completed in {elapsed:.1f}s")
        else:
            log.error(
                f"{node.name!r} failed with exit code {exit_code} after {elapsed:.1f}s"
            )
        return exit_code
