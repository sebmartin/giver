FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN pip install .

ENTRYPOINT ["python", "-m", "giver.kernel"]
