import json
import logging

from giver.kernel.logging import Logger


def test_creates_node_log_file(tmp_path):
    with Logger(tmp_path, ["node-a"]):
        logging.getLogger("node-a").info("hello")
    assert (tmp_path / "node-a.log").exists()


def test_creates_events_jsonl(tmp_path):
    with Logger(tmp_path, ["node-a"]):
        logging.getLogger("node-a").info("hello")
    assert (tmp_path / "events.jsonl").exists()


def test_events_jsonl_contains_log_lines(tmp_path):
    with Logger(tmp_path, ["node-a"]):
        logging.getLogger("node-a").info("hello")
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e["node"] == "node-a" and e["line"] == "hello" for e in events)


def test_context_manager_clears_handlers(tmp_path):
    with Logger(tmp_path, ["node-a"]):
        pass
    assert logging.getLogger("node-a").handlers == []


def test_no_handler_accumulation(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with Logger(tmp_path / "a", ["node-a"]):
        pass
    with Logger(tmp_path / "b", ["node-a"]):
        logging.getLogger("node-a").info("hello")
    assert (tmp_path / "b" / "node-a.log").read_text().count("hello") == 1


def test_unknown_logger_does_not_write_to_node_file(tmp_path):
    with Logger(tmp_path, ["node-a"]):
        logging.getLogger("node-b").info("should not appear")
    assert not (tmp_path / "node-b.log").exists()
