import json
import logging
from datetime import datetime, timezone
from pathlib import Path


class JSONLHandler(logging.FileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "node": record.name.split(".", 1)[-1],
            "line": record.getMessage(),
        }
        self.stream.write(json.dumps(event) + "\n")
        self.flush()


class Logger:
    def __init__(self, log_dir: Path, node_names: list[str]):
        self._loggers: list[logging.Logger] = []
        self._jsonl = JSONLHandler(log_dir / "events.jsonl")

        for name in node_names:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False

            stream = logging.StreamHandler()
            stream.setFormatter(logging.Formatter(f"[{name}] %(message)s"))

            node_file = logging.FileHandler(log_dir / f"{name}.log")

            for h in (stream, node_file, self._jsonl):
                logger.addHandler(h)

            self._loggers.append(logger)

    def close(self) -> None:
        for logger in self._loggers:
            for h in logger.handlers:
                if h is not self._jsonl:
                    h.close()
            logger.handlers.clear()
        self._jsonl.close()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()
