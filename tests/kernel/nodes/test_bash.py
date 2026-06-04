import logging

import pytest

from giver.kernel.nodes.bash import BashNode


async def test_run_returns_zero_on_success():
    node = BashNode(type="bash", name="test-node", command="exit 0")
    assert await node.run() == 0


async def test_run_returns_nonzero_on_failure():
    node = BashNode(type="bash", name="test-node", command="exit 42")
    assert await node.run() == 42


async def test_run_executes_command(tmp_path):
    marker = tmp_path / "marker"
    node = BashNode(type="bash", name="test-node", command=f"touch {marker}")
    await node.run()
    assert marker.exists()


async def test_run_logs_stdout(caplog):
    node = BashNode(type="bash", name="test-node", command="echo hello")
    with caplog.at_level(logging.INFO, logger="test-node"):
        await node.run()
    assert "hello" in caplog.text


async def test_run_logs_stderr(caplog):
    node = BashNode(type="bash", name="test-node", command="echo err >&2")
    with caplog.at_level(logging.INFO, logger="test-node"):
        await node.run()
    assert "err" in caplog.text


async def test_run_logs_multiline(caplog):
    node = BashNode(type="bash", name="test-node", command="printf 'a\nb\nc\n'")
    with caplog.at_level(logging.INFO, logger="test-node"):
        await node.run()
    assert ["a", "b", "c"] == [r.message for r in caplog.records]


def test_should_skip_false_when_no_output():
    node = BashNode(type="bash", name="n", command="true")
    assert node.should_skip() is False


def test_should_skip_false_when_output_missing():
    node = BashNode(type="bash", name="n", command="true", output="/nonexistent/path/file.md")
    assert node.should_skip() is False


def test_should_skip_true_when_output_exists(tmp_path):
    output = tmp_path / "result.md"
    output.write_text("done")
    node = BashNode(type="bash", name="n", command="true", output=str(output))
    assert node.should_skip() is True
