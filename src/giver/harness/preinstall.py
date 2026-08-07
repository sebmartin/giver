"""Shared `pre_install` commands.

An image deduplicates `pre_install` by exact string, so harnesses needing the
same thing get one layer between them by referencing the same constant here.
A harness needing something else declares its own command in its own class.
"""

# The agent CLIs want a newer node than Debian packages, so this adds the
# nodesource repository and installs from it.
NODE = (
    "apt-get update && "
    "apt-get install -y --no-install-recommends curl ca-certificates && "
    "curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && "
    "apt-get install -y --no-install-recommends nodejs && "
    "rm -rf /var/lib/apt/lists/*"
)
