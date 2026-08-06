FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent
RUN npm install -g @anthropic-ai/claude-code
RUN npm install -g @openai/codex

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN pip install .

# Unprivileged: a harness running unattended asks for no permission prompts, and
# root plus no prompts is a wider blast radius than either alone — claude-code
# refuses the combination outright. The container is still the isolation
# boundary; this just stops it being root inside its own box.
RUN useradd --create-home --uid 1000 giver
USER giver

# Each harness's state directory, so that a state volume mounted over one is
# seeded writable: Docker copies the image's ownership and mode onto a new named
# volume, and a root-owned mount point would be unusable by the user that has to
# log in through it. Mode 0777 because `giver run` runs the container as the
# uid of whoever invoked it, which is any number at all — the container holds a
# single user either way. Written as `~` because where a harness's state lands
# is give'r's decision, not the harness's.
RUN mkdir -p ~/.pi/agent ~/.claude ~/.codex && \
    chmod 0777 ~ ~/.pi ~/.pi/agent ~/.claude ~/.codex

ENTRYPOINT ["python", "-m", "giver.kernel"]
