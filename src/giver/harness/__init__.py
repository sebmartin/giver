"""The coding-agent CLIs give'r can run: pi and claude-code.

Each one gets a class holding what give'r needs to know about it — where it
stores credentials and sessions, what environment and ports it wants, how to
install it, how to start its REPL, which vendors it can serve, and how to run a
node's steps.

Split by concern so the harness classes can import what they need without
importing each other:

- `protocol`  the contract a harness implements, and the step it is handed
- `vendors`   turning `claude-opus-4-5` into `anthropic/claude-opus-4-5`
- `process`   subprocess plumbing shared by the CLI-backed harnesses
- `registry`  which harnesses exist, and looking one up by name
"""

from giver.harness.claude_code import ClaudeCodeHarness
from giver.harness.pi import PiHarness
from giver.harness.process import drain_stderr
from giver.harness.protocol import AgentStep, Harness
from giver.harness.registry import (
    DEFAULT_HARNESS,
    DEFAULT_HARNESS_NAME,
    HARNESS_NAMES,
    HARNESSES,
    HarnessName,
    harness_by_name,
)
from giver.harness.vendors import VENDOR_PREFIXES, resolve_model, vendor_of

__all__ = [
    "DEFAULT_HARNESS",
    "DEFAULT_HARNESS_NAME",
    "HARNESSES",
    "HARNESS_NAMES",
    "VENDOR_PREFIXES",
    "AgentStep",
    "ClaudeCodeHarness",
    "Harness",
    "HarnessName",
    "PiHarness",
    "drain_stderr",
    "harness_by_name",
    "resolve_model",
    "vendor_of",
]
