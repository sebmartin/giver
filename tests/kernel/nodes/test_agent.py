import json
import logging
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


def _pi_session(session_id: str) -> list[dict]:
    return [{"type": "session", "sessionId": session_id}]


# ── helpers: ClaudeRunner / claude CLI stream-json ───────────────────────────

def _claude_text(text: str, session_id: str = "s1") -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "session_id": session_id,
    }


def _claude_result(session_id: str, subtype: str = "success", is_error: bool = False) -> dict:
    return {"type": "result", "subtype": subtype, "is_error": is_error, "session_id": session_id}


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
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc) as m:
        result = await node.run()

    assert result == 1
    # one-shot per step: a step that never ran is a process that never spawned
    assert m.call_count == 1


# ── PiRunner: model switching ─────────────────────────────────────────────────

async def test_pi_runner_uses_step_model_over_default():
    """Each step spawns its own process, so model switching is just a per-step flag."""
    procs = [_mock_proc(_DONE), _mock_proc(_DONE)]
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", side_effect=procs) as m:
        await PiRunner().run(
            [AgentStep(prompt="step 1"), AgentStep(prompt="step 2", model="gpt-4-turbo")],
            default_model="gpt-4o",
            log=logging.getLogger("n"),
        )

    first_cmd, second_cmd = m.call_args_list[0][0], m.call_args_list[1][0]
    assert first_cmd[first_cmd.index("--model") + 1] == "gpt-4o"
    assert second_cmd[second_cmd.index("--model") + 1] == "gpt-4-turbo"


async def test_pi_runner_forks_session_from_first_step():
    """Fork rather than continue in place, so replay can't mutate the parent session."""
    procs = [_mock_proc(_pi_session("sess-abc") + _DONE), _mock_proc(_DONE)]
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", side_effect=procs) as m:
        await PiRunner().run(
            [AgentStep(prompt="p1"), AgentStep(prompt="p2")],
            default_model="gpt-4o",
            log=logging.getLogger("n"),
        )

    first_cmd, second_cmd = m.call_args_list[0][0], m.call_args_list[1][0]
    assert "--fork" not in first_cmd
    assert second_cmd[second_cmd.index("--fork") + 1] == "sess-abc"


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

async def test_claude_runner_logs_text_and_returns_0(caplog):
    proc = _mock_proc([_claude_text("hello"), _claude_result("s1")])
    with caplog.at_level(logging.INFO):
        with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
            result = await ClaudeRunner().run(
                [AgentStep(prompt="go")], default_model=None, log=logging.getLogger("t")
            )

    assert result == 0
    assert "hello" in caplog.text
    written = b"".join(c[0][0] for c in proc.stdin.write.call_args_list)
    assert written == b"go"


async def test_claude_runner_forks_session_from_first_step():
    """Resume must fork: bare --resume continues in place and mutates the parent
    session, which would corrupt the branch a checkpointed replay resumes from."""
    procs = [_mock_proc([_claude_result("sess-abc")]), _mock_proc([_claude_result("sess-xyz")])]
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", side_effect=procs) as m:
        await ClaudeRunner().run(
            [AgentStep(prompt="step1"), AgentStep(prompt="step2")],
            default_model=None,
            log=logging.getLogger("t"),
        )

    first_cmd, second_cmd = m.call_args_list[0][0], m.call_args_list[1][0]
    assert "--resume" not in first_cmd and "--fork-session" not in first_cmd
    assert second_cmd[second_cmd.index("--resume") + 1] == "sess-abc"
    assert "--fork-session" in second_cmd


async def test_claude_runner_passes_model_per_step():
    proc = _mock_proc([_claude_result("s1")])
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc) as m:
        await ClaudeRunner().run(
            [AgentStep(prompt="go", model="claude-opus-4")],
            default_model="claude-haiku-4",
            log=logging.getLogger("t"),
        )

    cmd = m.call_args_list[0][0]
    assert "--model" in cmd and "claude-opus-4" in cmd


async def test_claude_runner_returns_1_on_non_success():
    proc = _mock_proc([_claude_result("s1", subtype="error_max_turns", is_error=True)])
    with patch("giver.kernel.nodes.agent.asyncio.create_subprocess_exec", return_value=proc):
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
