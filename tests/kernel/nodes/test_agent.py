import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from giver.kernel.nodes.agent import AgentNode, AgentStep, _infer_provider


# ── helpers ──────────────────────────────────────────────────────────────────

class _AsyncLines:
    """Async iterator over pre-encoded JSONL lines."""
    def __init__(self, events: list[dict]):
        self._lines = iter((json.dumps(e) + "\n").encode() for e in events)

    def __aiter__(self): return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration


def _mock_proc(*event_batches: list[dict]) -> MagicMock:
    """One event_batch per step; each batch ends with agent_end."""
    all_events = [e for batch in event_batches for e in batch]
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdout = _AsyncLines(all_events)
    proc.stderr = _AsyncLines([])
    proc.wait = AsyncMock(return_value=0)
    return proc


_DONE = [{"type": "agent_end", "willRetry": False}]
_TEXT = [
    {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}},
    {"type": "agent_end", "willRetry": False},
]


def _written_commands(proc) -> list[dict]:
    raw = b"".join(c[0][0] for c in proc.stdin.write.call_args_list)
    return [json.loads(line) for line in raw.decode().splitlines() if line]


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_run_sends_single_prompt_and_returns_0():
    node = AgentNode(type="agent", name="n", steps=[AgentStep(prompt="hello")])
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=_mock_proc(_DONE)):
        assert await node.run() == 0


async def test_run_streams_text_deltas_to_log(caplog):
    node = AgentNode(type="agent", name="mynode", steps=[AgentStep(prompt="hi")])
    with caplog.at_level(logging.INFO, logger="mynode"):
        with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=_mock_proc(_TEXT)):
            await node.run()
    assert "hi" in caplog.text


async def test_run_returns_1_when_stdout_closes_without_agent_end():
    node = AgentNode(type="agent", name="n", steps=[AgentStep(prompt="p")])
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=_mock_proc([])):
        assert await node.run() == 1


async def test_run_sends_set_model_when_step_overrides():
    node = AgentNode(
        type="agent", name="n",
        model="claude-haiku-4",
        steps=[
            AgentStep(prompt="step 1"),
            AgentStep(prompt="step 2", model="claude-opus-4"),
        ],
    )
    proc = _mock_proc(_DONE, _DONE)
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
        await node.run()

    cmds = _written_commands(proc)
    assert {"type": "set_model", "provider": "anthropic", "modelId": "claude-opus-4"} in cmds


async def test_run_no_set_model_when_step_uses_node_default():
    node = AgentNode(
        type="agent", name="n",
        model="claude-haiku-4",
        steps=[AgentStep(prompt="p1"), AgentStep(prompt="p2")],
    )
    proc = _mock_proc(_DONE, _DONE)
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
        await node.run()

    cmds = _written_commands(proc)
    assert not any(c.get("type") == "set_model" for c in cmds)


async def test_run_stops_after_failed_step():
    node = AgentNode(
        type="agent", name="n",
        steps=[AgentStep(prompt="p1"), AgentStep(prompt="p2")],
    )
    failed = [{"type": "agent_end", "willRetry": True}]
    proc = _mock_proc(failed)
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
        result = await node.run()

    assert result == 1
    prompts = [c["message"] for c in _written_commands(proc) if c.get("type") == "prompt"]
    assert prompts == ["p1"]


async def test_yaml_loads_as_agent_node_with_steps(workflows_dir):
    from giver.kernel.workflow import Workflow
    wf = Workflow.from_file(workflows_dir / "single_node_agent.yaml")
    node = wf.nodes[0]
    assert isinstance(node, AgentNode)
    assert node.steps[0].prompt == "say hello"


async def test_yaml_loads_model_and_per_step_override(workflows_dir):
    from giver.kernel.workflow import Workflow
    wf = Workflow.from_file(workflows_dir / "single_node_agent_with_model.yaml")
    node = wf.nodes[0]
    assert isinstance(node, AgentNode)
    assert node.model == "claude-haiku-4"
    assert node.steps[1].model == "claude-opus-4"


@pytest.mark.parametrize("model,expected", [
    ("claude-opus-4", "anthropic"),
    ("claude-haiku-4", "anthropic"),
    ("gpt-4o", "openai"),
    ("o3-mini", "openai"),
    ("unknown-model", "anthropic"),
])
def test_infer_provider(model, expected):
    assert _infer_provider(model) == expected
