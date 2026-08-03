import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from giver.harness import (
    DEFAULT_HARNESS_NAME,
    AgentStep,
    HarnessName,
    harness_by_name,
    resolve_model,
    vendor_of,
)

if TYPE_CHECKING:
    from giver.kernel.workflow import Defaults

__all__ = ["AgentNode", "AgentStep"]


class AgentNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["agent"]
    name: str
    depends_on: list[str] = []
    harness_name: HarnessName = Field(default=None, alias="harness")
    model: str | None = None
    output: str | None = None
    steps: list[AgentStep]

    def apply_defaults(self, defaults: "Defaults") -> None:
        """Resolve harness and model down to every step, then validate.

        Done at load time so a workflow that can't resolve fails before any
        container exists, and so `run()` never has to reason about defaults.
        """
        # Resolve to a concrete name at load time, so nothing downstream has to
        # re-derive what "unset" means.
        self.harness_name = (
            self.harness_name or defaults.harness_name or DEFAULT_HARNESS_NAME
        )
        node_model = self.model or defaults.model
        for step in self.steps:
            model = step.model or node_model
            if model is None:
                raise ValueError(
                    f"node {self.name!r}: no model for step {step.prompt[:40]!r} — "
                    "set model on the step, the node, or workflow defaults"
                )
            step.model = resolve_model(model)

        vendors = {vendor_of(step.model) for step in self.steps}
        if len(vendors) > 1:
            raise ValueError(
                f"node {self.name!r} mixes vendors ({', '.join(sorted(vendors))}). "
                "Cross-vendor collaboration happens between nodes — split the steps "
                "into separate nodes and pass artifacts."
            )
        harness = harness_by_name(self.harness_name)
        vendor = vendors.pop()
        if not harness.serves(vendor):
            raise ValueError(
                f"node {self.name!r}: harness {harness.name!r} does not serve "
                f"vendor {vendor!r}"
            )

    def should_skip(self) -> bool:
        return self.output is not None and Path(self.output).exists()

    async def run(self) -> int:
        log = logging.getLogger(self.name)
        return await harness_by_name(self.harness_name).run(self.steps, log)
