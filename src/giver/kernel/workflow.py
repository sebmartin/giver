import asyncio
import logging
import time
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from giver.harness import HarnessName
from giver.kernel.logging import Logger
from giver.kernel.nodes import Node, NodeField


class Defaults(BaseModel):
    """Workflow-level settings a node inherits unless it says otherwise.

    `model` reaches the step; `harness` stops at the node — the steps of one
    node share a session, and session ids belong to the harness that issued
    them.
    """

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    harness: HarnessName = None


class Workflow(BaseModel):
    name: str
    defaults: Defaults = Defaults()
    nodes: list[NodeField]

    @classmethod
    def from_file(cls, path: Path) -> "Workflow":
        return cls.model_validate(yaml.safe_load(path.read_text()))

    @model_validator(mode="after")
    def _apply_defaults(self) -> "Workflow":
        # Before the DAG check, so a workflow with both problems reports the
        # cheaper one first.
        for node in self.nodes:
            node.apply_defaults(self.defaults)
        return self

    @model_validator(mode="after")
    def _validate_dag(self) -> "Workflow":
        deps = {}
        for node in self.nodes:
            if node.name in deps:
                raise ValueError(f"duplicate node name {node.name!r}")
            deps[node.name] = node.depends_on
        for name, node_deps in deps.items():
            for dep in node_deps:
                if dep not in deps:
                    raise ValueError(f"{name!r} depends on unknown node {dep!r}")
        # Kahn's algorithm: if some node never reaches indegree 0, there's a cycle.
        indegree = {name: len(d) for name, d in deps.items()}
        ready = [name for name, n in indegree.items() if n == 0]
        resolved = 0
        while ready:
            done = ready.pop()
            resolved += 1
            for name, node_deps in deps.items():
                if done in node_deps:
                    indegree[name] -= 1
                    if indegree[name] == 0:
                        ready.append(name)
        if resolved != len(deps):
            raise ValueError("workflow has a dependency cycle")
        return self

    async def run(self, log_dir: Path) -> bool:
        log_dir.mkdir(parents=True, exist_ok=True)
        with Logger(log_dir, [n.name for n in self.nodes]):
            # create_task is synchronous, so every task exists before any body
            # runs and reaches its first await — deps are always present.
            tasks: dict[str, asyncio.Task[bool]] = {}
            for node in self.nodes:
                tasks[node.name] = asyncio.create_task(self._run_when_ready(node, tasks))
            results = await asyncio.gather(*tasks.values())
            return all(results)

    async def _run_when_ready(
        self, node: Node, tasks: dict[str, "asyncio.Task[bool]"]
    ) -> bool:
        deps_ok = await asyncio.gather(*(tasks[d] for d in node.depends_on))
        if not all(deps_ok):
            logging.getLogger(node.name).warning(
                f"skipping {node.name!r}: a dependency did not succeed"
            )
            return False
        if node.should_skip():
            logging.getLogger(node.name).info(
                f"skipping {node.name!r}: output already exists"
            )
            return True
        return await self._run_node(node) == 0

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
