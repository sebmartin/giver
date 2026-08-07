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
# The home directory is traversable by anyone, because the uid that will use it
# is not known here — `giver run` runs the container as the user who invoked it,
# so that the run output it writes to a bind mount belongs to the person who
# will read it. Everything a harness stores lives on a volume mounted beneath
# this directory, owned by that user; the directory itself is only a path to
# walk through.
RUN useradd --create-home --uid 1000 giver && chmod 0755 /home/giver
USER giver

ENTRYPOINT ["python", "-m", "giver.kernel"]
