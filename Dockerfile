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

ENTRYPOINT ["python", "-m", "giver.kernel"]
