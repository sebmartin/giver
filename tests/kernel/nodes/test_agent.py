import json
import logging
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from giver.kernel.nodes.agent import (
    AgentNode,
    AgentStep,
    ClaudeRunner,
    PiRunner,
    _infer_provider,
)


# ── helpers: PiRunner / subprocess ───────────────────────────────────────────

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


# ── helpers: ClaudeRunner / fake SDK ─────────────────────────────────────────

class _TextBlock:
    def __init__(self, text: str):
        self.text = text


class _AssistantMessage:
    def __init__(self, *blocks):
        self.content = list(blocks)


class _ResultMessage:
    def __init__(self, session_id: str, subtype: str = "success"):
        self.session_id = session_id
        self.subtype = subtype


class _ClaudeAgentOptions:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _fake_sdk(calls_out: list, events_by_call: list[list]) -> types.ModuleType:
    """Fake claude_agent_sdk; appends {prompt, options} to calls_out per query call."""
    module = types.ModuleType("claude_agent_sdk")
    module.TextBlock = _TextBlock
    module.AssistantMessage = _AssistantMessage
    module.ResultMessage = _ResultMessage
    module.ClaudeAgentOptions = _ClaudeAgentOptions

    async def query(prompt, options=None):
        idx = len(calls_out)
        calls_out.append({"prompt": prompt, "options": options})
        for event in events_by_call[idx]:
            yield event

    module.query = query
    return module


# ── AgentNode: no model → PiRunner via subprocess ────────────────────────────

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


# ── PiRunner: model switching ─────────────────────────────────────────────────

async def test_pi_runner_sends_set_model_when_step_overrides():
    steps = [AgentStep(prompt="step 1"), AgentStep(prompt="step 2", model="gpt-4-turbo")]
    proc = _mock_proc(_DONE, _DONE)
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
        await PiRunner().run(steps, default_model="gpt-4o", log=logging.getLogger("n"))

    cmds = _written_commands(proc)
    assert {"type": "set_model", "provider": "openai", "modelId": "gpt-4-turbo"} in cmds


async def test_pi_runner_no_set_model_when_step_uses_default():
    steps = [AgentStep(prompt="p1"), AgentStep(prompt="p2")]
    proc = _mock_proc(_DONE, _DONE)
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
        await PiRunner().run(steps, default_model="gpt-4o", log=logging.getLogger("n"))

    cmds = _written_commands(proc)
    assert not any(c.get("type") == "set_model" for c in cmds)


# ── AgentNode: load-time runner validation ────────────────────────────────────

def test_node_raises_on_mixed_vendors():
    with pytest.raises(ValueError, match="mixes runners"):
        AgentNode(type="agent", name="n", steps=[
            AgentStep(prompt="a", model="claude-haiku-4"),
            AgentStep(prompt="b", model="gpt-4o"),
        ])


def test_node_accepts_same_vendor_steps():
    AgentNode(type="agent", name="n", steps=[
        AgentStep(prompt="a", model="claude-haiku-4"),
        AgentStep(prompt="b", model="claude-opus-4"),
    ])


# ── ClaudeRunner ──────────────────────────────────────────────────────────────

async def test_claude_runner_logs_text_and_returns_0(caplog, monkeypatch):
    calls: list = []
    events = [[_AssistantMessage(_TextBlock("hello")), _ResultMessage("s1")]]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(calls, events))

    with caplog.at_level(logging.INFO):
        result = await ClaudeRunner().run(
            [AgentStep(prompt="go")], default_model=None, log=logging.getLogger("t")
        )

    assert result == 0
    assert "hello" in caplog.text
    assert calls[0]["prompt"] == "go"


async def test_claude_runner_passes_session_id_on_second_step(monkeypatch):
    calls: list = []
    events = [
        [_ResultMessage("sess-abc")],
        [_ResultMessage("sess-xyz")],
    ]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(calls, events))

    await ClaudeRunner().run(
        [AgentStep(prompt="step1"), AgentStep(prompt="step2")],
        default_model=None,
        log=logging.getLogger("t"),
    )

    assert calls[0]["options"].resume is None
    assert calls[1]["options"].resume == "sess-abc"


async def test_claude_runner_returns_1_on_non_success(monkeypatch):
    calls: list = []
    events = [[_ResultMessage("s1", subtype="error_max_turns")]]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", _fake_sdk(calls, events))

    result = await ClaudeRunner().run(
        [AgentStep(prompt="go")], default_model=None, log=logging.getLogger("t")
    )
    assert result == 1


# ── YAML loading ──────────────────────────────────────────────────────────────

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


# ── _infer_provider ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected", [
    ("claude-opus-4", "anthropic"),
    ("claude-haiku-4", "anthropic"),
    ("gpt-4o", "openai"),
    ("o3-mini", "openai"),
    ("unknown-model", None),
    (None, None),
])
def test_infer_provider(model, expected):
    assert _infer_provider(model) == expected
