"""The layer between give'r and whatever agent harness you want to run.

give'r orchestrates workflows; a harness does the agent work. Keeping the two
apart is what stops give'r from being welded to any one vendor's CLI — first
party or third. A harness is described here in terms give'r can act on, and the
rest of the codebase talks to that description rather than to pi or claude-code
directly. Adding a harness is adding a class; nothing else has to learn about
it.

Split by concern so the harness classes can import what they need without
importing each other:

- `protocol`  what a harness has to provide, and the step it is handed
- `vendors`   turning `claude-opus-4-5` into `anthropic/claude-opus-4-5`
- `process`   subprocess plumbing shared by the CLI-backed harnesses
- `registry`  which harnesses exist, and looking one up by name
- `toolchains` commands a harness's `install` needs to already be there
"""

from giver.harness.claude_code import ClaudeCodeHarness
from giver.harness.codex import CodexHarness
from giver.harness.pi import PiHarness
from giver.harness.process import spawn
from giver.harness.protocol import AgentStep, Harness
from giver.harness.registry import (
    DEFAULT_HARNESS,
    DEFAULT_HARNESS_NAME,
    HARNESS_NAMES,
    HARNESSES,
    HarnessName,
    harness_by_name,
)
from giver.harness.toolchains import NODE
from giver.harness.vendors import VENDOR_PREFIXES, resolve_model, vendor_of

__all__ = [
    "DEFAULT_HARNESS",
    "DEFAULT_HARNESS_NAME",
    "HARNESSES",
    "HARNESS_NAMES",
    "NODE",
    "VENDOR_PREFIXES",
    "AgentStep",
    "ClaudeCodeHarness",
    "CodexHarness",
    "Harness",
    "HarnessName",
    "PiHarness",
    "harness_by_name",
    "resolve_model",
    "spawn",
    "vendor_of",
]
