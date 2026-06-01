import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from giver.kernel.nodes.bash import BashNode
from giver.workflow import Workflow

WORKFLOWS = Path(__file__).parent / "workflows"


def test_load_single_node_bash(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_bash.yaml")
    assert wf.name == "single-node-bash"
    assert len(wf.nodes) == 1
    node = wf.nodes[0]
    assert isinstance(node, BashNode)
    assert node.name == "greet"
    assert node.command == 'echo "hello from giver"'


def test_load_unknown_node_type(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: test\nnodes:\n  - name: x\n    type: unknown\n    command: echo hi\n")
    with pytest.raises(ValidationError):
        Workflow.from_file(bad)


async def test_run_creates_node_log(workflows_dir, tmp_path):
    wf = Workflow.from_file(workflows_dir / "single_node_bash.yaml")
    await wf.run(tmp_path)
    assert "hello from giver" in (tmp_path / "greet.log").read_text()


async def test_run_creates_events_jsonl(workflows_dir, tmp_path):
    wf = Workflow.from_file(workflows_dir / "single_node_bash.yaml")
    await wf.run(tmp_path)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e["node"] == "greet" for e in events)


async def test_run_no_handler_accumulation(workflows_dir, tmp_path):
    wf = Workflow.from_file(workflows_dir / "single_node_bash.yaml")
    await wf.run(tmp_path / "a")
    await wf.run(tmp_path / "b")
    assert (tmp_path / "b" / "greet.log").read_text().count("hello from giver") == 1


async def test_run_logs_failure(tmp_path):
    wf = Workflow(name="fail", nodes=[BashNode(type="bash", name="boom", command="exit 3")])
    await wf.run(tmp_path)
    assert "exit code 3" in (tmp_path / "boom.log").read_text()
