from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from giver.harness import AgentStep
from giver.kernel.nodes.agent import AgentNode
from giver.kernel.workflow import Defaults, Workflow


def node(*steps: AgentStep, **kwargs) -> AgentNode:
    return AgentNode(type="agent", name="n", steps=list(steps), **kwargs)


# ── resolving harness and model ───────────────────────────────────────────────


def test_inherits_model_from_defaults():
    n = node(AgentStep(prompt="a"))
    n.apply_defaults(Defaults(model="anthropic/claude-haiku-4-5"))
    assert [s.model for s in n.steps] == ["anthropic/claude-haiku-4-5"]


def test_nearest_declaration_wins():
    n = node(
        AgentStep(prompt="a"),
        AgentStep(prompt="b", model="anthropic/claude-opus-4-5"),
        model="anthropic/claude-sonnet-4-5",
    )
    n.apply_defaults(Defaults(model="anthropic/claude-haiku-4-5"))
    assert [s.model for s in n.steps] == [
        "anthropic/claude-sonnet-4-5",
        "anthropic/claude-opus-4-5",
    ]


def test_bare_model_names_are_qualified():
    n = node(AgentStep(prompt="a", model="claude-opus-4-5"))
    n.apply_defaults(Defaults())
    assert n.steps[0].model == "anthropic/claude-opus-4-5"


def test_inherits_harness_from_defaults():
    n = node(AgentStep(prompt="a", model="anthropic/claude-opus-4-5"))
    n.apply_defaults(Defaults(harness="claude-code"))
    assert n.harness_name == "claude-code"


def test_unset_harness_resolves_to_the_default():
    """Resolved at load rather than left as None, so nothing downstream has to
    re-derive what unset means."""
    n = node(AgentStep(prompt="a", model="openai/gpt-5.5"))
    n.apply_defaults(Defaults())
    assert n.harness_name == "pi"


def test_an_unknown_harness_fails_when_the_node_is_built():
    with pytest.raises(ValidationError, match="unknown harness 'clyde'"):
        node(AgentStep(prompt="a", model="openai/gpt-5.5"), harness="clyde")


# ── load-time errors ──────────────────────────────────────────────────────────


def test_a_step_with_no_model_anywhere_is_an_error():
    n = node(AgentStep(prompt="do the thing"))
    with pytest.raises(ValueError, match="no model for step"):
        n.apply_defaults(Defaults())


def test_mixing_vendors_in_one_node_is_an_error():
    n = node(
        AgentStep(prompt="a", model="anthropic/claude-opus-4-5"),
        AgentStep(prompt="b", model="openai/gpt-5.5"),
    )
    with pytest.raises(ValueError, match="mixes vendors"):
        n.apply_defaults(Defaults())


def test_a_harness_that_cannot_serve_the_vendor_is_an_error():
    n = node(AgentStep(prompt="a", model="openai/gpt-5.5"), harness="claude-code")
    with pytest.raises(ValueError, match="does not serve vendor 'openai'"):
        n.apply_defaults(Defaults())


def test_claude_models_may_run_on_pi():
    """Anthropic on an API key is legitimate — the harness is the user's call."""
    n = node(AgentStep(prompt="a", model="anthropic/claude-opus-4-5"), harness="pi")
    n.apply_defaults(Defaults())  # no raise


def test_a_step_cannot_name_a_harness():
    """The steps of a node share one session, and session ids belong to the
    harness that issued them — so this must fail rather than be ignored."""
    with pytest.raises(ValidationError, match="harness"):
        AgentStep(prompt="a", model="anthropic/claude-opus-4-5", harness="pi")


# ── behaviour ─────────────────────────────────────────────────────────────────


async def test_run_delegates_to_the_named_harness():
    n = node(AgentStep(prompt="a", model="anthropic/claude-opus-4-5"), harness="claude-code")
    n.apply_defaults(Defaults())

    with patch("giver.kernel.nodes.agent.harness_by_name") as by_name:
        by_name.return_value.run = AsyncMock(return_value=0)
        assert await n.run() == 0

    by_name.assert_called_with("claude-code")
    assert by_name.return_value.run.call_args[0][0] == n.steps


async def test_run_prepares_the_harness_before_using_it():
    """Mounting a harness's state is only half of provisioning it — one that
    keeps state elsewhere reconciles that in `prepare`, and a node reached
    without give'r's CLI (CI running the kernel directly) has no other chance."""
    n = node(AgentStep(prompt="a", model="anthropic/claude-opus-4-5"), harness="claude-code")
    n.apply_defaults(Defaults())

    calls = []
    with patch("giver.kernel.nodes.agent.harness_by_name") as by_name:
        harness = by_name.return_value
        harness.prepare = lambda: calls.append("prepare")
        harness.run = AsyncMock(side_effect=lambda *a: calls.append("run") or 0)
        await n.run()

    assert calls == ["prepare", "run"]


def test_should_skip_when_the_output_exists(tmp_path):
    existing = tmp_path / "out.md"
    existing.write_text("done")
    assert node(AgentStep(prompt="a"), output=str(existing)).should_skip()
    assert not node(AgentStep(prompt="a"), output=str(tmp_path / "missing.md")).should_skip()


# ── YAML loading ──────────────────────────────────────────────────────────────


def test_yaml_loads_as_agent_node_with_steps(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_agent.yaml")
    node = wf.nodes[0]
    assert isinstance(node, AgentNode)
    assert node.steps[0].prompt == "say hello"
    assert node.steps[0].model == "anthropic/claude-haiku-4-5"


def test_yaml_loads_model_and_per_step_override(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_agent_with_model.yaml")
    node = wf.nodes[0]
    assert [s.model for s in node.steps] == [
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4-5",
    ]
