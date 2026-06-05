import json
import time

import pytest
from pydantic import ValidationError

from giver.kernel.nodes.bash import BashNode
from giver.kernel.workflow import Workflow


def _node_order(log_dir):
    """Distinct node names in order of first appearance in events.jsonl."""
    events = [json.loads(l) for l in (log_dir / "events.jsonl").read_text().splitlines()]
    order = []
    for e in events:
        if e["node"] not in order:
            order.append(e["node"])
    return order


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


async def test_depends_on_runs_in_dependency_order(workflows_dir, tmp_path):
    wf = Workflow.from_file(workflows_dir / "linear_chain.yaml")
    await wf.run(tmp_path)
    assert _node_order(tmp_path) == ["a", "b", "c"]


async def test_independent_nodes_run_concurrently(workflows_dir, tmp_path):
    # b and c each sleep 0.1s; serial would be ~0.2s, parallel ~0.1s.
    wf = Workflow.from_file(workflows_dir / "diamond.yaml")
    started = time.monotonic()
    await wf.run(tmp_path)
    assert time.monotonic() - started < 0.18


def _chain(tmp_path, head_command):
    """a runs head_command; b depends on a and touches b_marker; c depends on b."""
    return Workflow(
        name="chain",
        nodes=[
            BashNode(type="bash", name="a", command=head_command),
            BashNode(type="bash", name="b", command=f"touch {tmp_path / 'b_marker'}", depends_on=["a"]),
            BashNode(type="bash", name="c", command=f"touch {tmp_path / 'c_marker'}", depends_on=["b"]),
        ],
    )


async def test_skips_dependent_when_dependency_fails(tmp_path):
    await _chain(tmp_path, head_command="exit 1").run(tmp_path)
    assert not (tmp_path / "b_marker").exists()
    assert "skipping" in (tmp_path / "b.log").read_text()


async def test_skip_propagates_transitively(tmp_path):
    await _chain(tmp_path, head_command="exit 1").run(tmp_path)
    assert not (tmp_path / "c_marker").exists()


async def test_successful_dependency_lets_dependent_run(tmp_path):
    await _chain(tmp_path, head_command="exit 0").run(tmp_path)
    assert (tmp_path / "b_marker").exists()
    assert (tmp_path / "c_marker").exists()


def test_rejects_unknown_dependency():
    with pytest.raises(ValidationError):
        Workflow(
            name="bad",
            nodes=[BashNode(type="bash", name="a", command="true", depends_on=["ghost"])],
        )


def test_rejects_cycle():
    with pytest.raises(ValidationError):
        Workflow(
            name="bad",
            nodes=[
                BashNode(type="bash", name="a", command="true", depends_on=["b"]),
                BashNode(type="bash", name="b", command="true", depends_on=["a"]),
            ],
        )


def test_rejects_duplicate_names():
    with pytest.raises(ValidationError):
        Workflow(
            name="bad",
            nodes=[
                BashNode(type="bash", name="dup", command="true"),
                BashNode(type="bash", name="dup", command="true"),
            ],
        )
