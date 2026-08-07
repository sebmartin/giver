"""Commands that install what a harness's own `install` depends on.

A harness declares the command itself, not a name something else resolves —
nothing reads these values, and no table maps them to anything. They exist as
shared constants for one reason: two harnesses needing the same prerequisite
produce identical strings, and identical strings deduplicate. A harness needing
something else writes its own command and gets its own layer, with no code
anywhere learning what it is.
"""

# node is not a Debian package at the version the agent CLIs want, so this is
# the nodesource repository plus an install rather than a package name.
NODE = (
    "apt-get update && "
    "apt-get install -y --no-install-recommends curl ca-certificates && "
    "curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && "
    "apt-get install -y --no-install-recommends nodejs && "
    "rm -rf /var/lib/apt/lists/*"
)
