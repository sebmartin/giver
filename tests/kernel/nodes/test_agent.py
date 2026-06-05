import os

import pytest

from giver.kernel.nodes.agent import AgentNode
from giver.kernel.workflow import Workflow


@pytest.fixture
def fake_pi(monkeypatch, tmp_path):
    """Put a stub `pi` on PATH that echoes back the prompt it received.

    Echoing $4 (the -p value) proves shell quoting survived: if it broke, a
    prompt with spaces/quotes would split across argv and $4 would be partial.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pi = bindir / "pi"
    pi.write_text('#!/bin/sh\necho "PROMPT:$4"\n')
    pi.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")


def test_prompt_compiles_to_pi_command():
    node = AgentNode(type="agent", name="plan", prompt="hello")
    assert node.command == "pi --mode json -p hello"


def test_prompt_with_special_chars_is_quoted():
    node = AgentNode(type="agent", name="plan", prompt="it's a plan")
    assert node.command == """pi --mode json -p 'it'"'"'s a plan'"""


def test_model_absent_produces_no_flag():
    node = AgentNode(type="agent", name="plan", prompt="hello")
    assert "--model" not in node.command


def test_model_set_is_included_in_command():
    node = AgentNode(type="agent", name="plan", prompt="hello", model="claude-opus-4-8")
    assert node.command == "pi --mode json --model claude-opus-4-8 -p hello"


def test_model_with_spaces_is_quoted():
    node = AgentNode(type="agent", name="plan", prompt="hello", model="foo bar")
    assert node.command == "pi --mode json --model 'foo bar' -p hello"


def test_yaml_loads_as_agent_node(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_agent.yaml")
    node = wf.nodes[0]
    assert isinstance(node, AgentNode)
    assert node.prompt == "Analyze the codebase and produce a plan"


def test_yaml_loads_model_field(workflows_dir):
    wf = Workflow.from_file(workflows_dir / "single_node_agent_with_model.yaml")
    node = wf.nodes[0]
    assert isinstance(node, AgentNode)
    assert node.model == "claude-opus-4-8"
    assert node.command == "pi --mode json --model claude-opus-4-8 -p 'Analyze the codebase'"


async def test_agent_node_runs_pi_and_logs_output(fake_pi, tmp_path):
    wf = Workflow(name="agent", nodes=[AgentNode(type="agent", name="plan", prompt="it's a plan")])
    await wf.run(tmp_path)
    assert "PROMPT:it's a plan" in (tmp_path / "plan.log").read_text()
