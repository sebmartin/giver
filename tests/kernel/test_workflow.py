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


async def test_node_with_existing_output_is_skipped(tmp_path):
    output = tmp_path / "result.md"
    output.write_text("already done")
    marker = tmp_path / "marker"
    wf = Workflow(
        name="idempotent",
        nodes=[BashNode(type="bash", name="work", command=f"touch {marker}", output=str(output))],
    )
    await wf.run(tmp_path)
    assert not marker.exists()
    assert "already exists" in (tmp_path / "work.log").read_text()


async def test_node_with_missing_output_runs_normally(tmp_path):
    output = tmp_path / "result.md"
    marker = tmp_path / "marker"
    wf = Workflow(
        name="idempotent",
        nodes=[BashNode(type="bash", name="work", command=f"touch {marker}", output=str(output))],
    )
    await wf.run(tmp_path)
    assert marker.exists()


async def test_skipped_node_does_not_block_dependents(tmp_path):
    output = tmp_path / "result.md"
    output.write_text("already done")
    marker = tmp_path / "marker"
    wf = Workflow(
        name="idempotent",
        nodes=[
            BashNode(type="bash", name="a", command="true", output=str(output)),
            BashNode(type="bash", name="b", command=f"touch {marker}", depends_on=["a"]),
        ],
    )
    await wf.run(tmp_path)
    assert marker.exists()


async def test_run_returns_true_when_all_nodes_succeed(tmp_path, workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_bash.yaml")
    assert await wf.run(tmp_path) is True


# ── defaults cascade ──────────────────────────────────────────────────────────


def test_defaults_cascade_to_every_step(workflows_dir):
    """`model` reaches the step, `harness` stops at the node, nearest wins —
    and bare names are qualified at load so no harness ever sees one."""
    wf = Workflow.from_file(workflows_dir / "defaults_cascade.yaml")
    resolved = {n.name: (n.harness, [s.model for s in n.steps]) for n in wf.nodes}

    assert resolved == {
        "inherits-everything": ("claude-code", ["anthropic/claude-haiku-4-5"]),
        "overrides-model-at-node": (
            "claude-code",
            ["anthropic/claude-opus-4-5", "anthropic/claude-haiku-4-5"],
        ),
        "overrides-harness-at-node": ("pi", ["anthropic/claude-haiku-4-5"]),
    }


def test_workflow_without_defaults_still_loads(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_agent_with_model.yaml")
    assert wf.defaults.model is None and wf.defaults.harness is None


def test_missing_model_fails_at_load_naming_the_step():
    with pytest.raises(ValidationError, match="no model for step"):
        Workflow.model_validate(
            {"name": "t", "nodes": [
                {"name": "n", "type": "agent", "steps": [{"prompt": "do the thing"}]}
            ]}
        )


def test_ambiguous_bare_model_fails_at_load():
    with pytest.raises(ValidationError, match="write it as vendor/qwen-2.5-coder"):
        Workflow.model_validate(
            {"name": "t", "defaults": {"model": "qwen-2.5-coder"}, "nodes": [
                {"name": "n", "type": "agent", "steps": [{"prompt": "a"}]}
            ]}
        )
