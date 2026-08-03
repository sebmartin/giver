from typing import Annotated

from pydantic import AfterValidator

from giver.harness.claude_code import ClaudeCodeHarness
from giver.harness.pi import PiHarness
from giver.harness.protocol import Harness

HARNESSES: tuple[Harness, ...] = (ClaudeCodeHarness(), PiHarness())

# pi is the batteries-included path: it works in its default configuration and
# serves any vendor by API key. Named rather than taken from the end of the
# tuple, so it reads as a choice and can become configuration later.
DEFAULT_HARNESS_NAME = "pi"

HARNESS_NAMES = tuple(h.name for h in HARNESSES)


def harness_by_name(name: str | None) -> Harness:
    """The harness a workflow named, or the default one."""
    if name is None:
        name = DEFAULT_HARNESS_NAME
    for harness in HARNESSES:
        if harness.name == name:
            return harness
    raise ValueError(f"unknown harness {name!r}. choices: {', '.join(HARNESS_NAMES)}")


DEFAULT_HARNESS: Harness = harness_by_name(DEFAULT_HARNESS_NAME)


def _known_harness(value: str | None) -> str | None:
    if value is not None:
        harness_by_name(value)  # raises, listing the registered names
    return value


# Use this instead of a plain `str` for any field naming a harness: a typo is
# caught when the workflow loads, with the valid names in the message, and the
# list comes from the registry rather than being written out again.
HarnessName = Annotated[str | None, AfterValidator(_known_harness)]
