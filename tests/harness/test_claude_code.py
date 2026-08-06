import logging
from unittest.mock import patch

import pytest

from giver.harness import AgentStep, ClaudeCodeHarness


def result_event(session_id: str, subtype: str = "success", is_error: bool = False) -> dict:
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "session_id": session_id,
    }


def assistant(text: str, session_id: str = "s1") -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
        "session_id": session_id,
    }


def step(prompt: str, model: str = "anthropic/claude-opus-4-5") -> AgentStep:
    return AgentStep(prompt=prompt, model=model)


async def run(steps, procs, log=None):
    with patch(
        "giver.harness.process.asyncio.create_subprocess_exec", side_effect=procs
    ) as spawn:
        result = await ClaudeCodeHarness().run(steps, log or logging.getLogger("n"))
    return result, spawn


async def test_strips_the_vendor_from_the_model(mock_proc):
    """claude wants the bare name; the vendor prefix is give'r's routing input."""
    _, spawn = await run([step("go", "anthropic/claude-opus-4-5")], [mock_proc([result_event("s1")])])

    cmd = spawn.call_args_list[0][0]
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-5"


async def test_logs_assistant_text_and_succeeds(mock_proc, caplog):
    proc = mock_proc([assistant("hello"), result_event("s1")])
    with caplog.at_level(logging.INFO):
        result, _ = await run([step("go")], [proc])

    assert result == 0
    assert "hello" in caplog.text
    written = b"".join(c[0][0] for c in proc.stdin.write.call_args_list)
    assert written == b"go"


async def test_forks_when_resuming(mock_proc):
    """--fork-session is opt-in: a bare --resume continues in place and mutates
    the parent session, which would corrupt the branch a replay resumed from."""
    procs = [mock_proc([result_event("sess-abc")]), mock_proc([result_event("sess-xyz")])]
    _, spawn = await run([step("p1"), step("p2")], procs)

    first, second = spawn.call_args_list[0][0], spawn.call_args_list[1][0]
    assert "--resume" not in first and "--fork-session" not in first
    assert second[second.index("--resume") + 1] == "sess-abc"
    assert "--fork-session" in second


async def test_fails_on_a_non_success_result(mock_proc):
    proc = mock_proc([result_event("s1", subtype="error_max_turns", is_error=True)])
    result, _ = await run([step("go")], [proc])
    assert result == 1


async def test_fails_on_nonzero_exit_despite_a_clean_stream(mock_proc):
    result, _ = await run([step("go")], [mock_proc([result_event("s1")], exit_code=1)])
    assert result == 1


# ── prepare ───────────────────────────────────────────────────────────────────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home directory whose `~/.claude` is the mounted state directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    return tmp_path


def test_prepare_puts_the_config_inside_the_state_directory(home):
    """`~/.claude.json` is a sibling of `~/.claude`, so persisting the state
    directory alone keeps the credentials and loses the config — which is what
    carries onboarding and the account identity."""
    ClaudeCodeHarness().prepare()

    (home / ".claude.json").write_text('{"hasCompletedOnboarding": true}')

    assert (home / ".claude/claude.json").read_text() == '{"hasCompletedOnboarding": true}'


def test_prepare_is_idempotent(home):
    harness = ClaudeCodeHarness()
    harness.prepare()
    (home / ".claude.json").write_text('{"kept": true}')
    harness.prepare()

    assert (home / ".claude/claude.json").read_text() == '{"kept": true}'


def test_prepare_adopts_a_config_written_before_the_link_existed(home):
    (home / ".claude.json").write_text('{"oauthAccount": "someone"}')

    ClaudeCodeHarness().prepare()

    assert (home / ".claude.json").is_symlink()
    assert (home / ".claude/claude.json").read_text() == '{"oauthAccount": "someone"}'


def test_prepare_leaves_a_real_config_alone_when_the_state_directory_has_one(home):
    """give'r is meant to run without a container too, where `~/.claude.json` is
    someone's actual config. Two real files is not a case to resolve by
    guessing — neither is destroyed."""
    (home / ".claude.json").write_text('{"which": "home"}')
    (home / ".claude/claude.json").write_text('{"which": "state"}')

    ClaudeCodeHarness().prepare()

    assert (home / ".claude.json").read_text() == '{"which": "home"}'
    assert (home / ".claude/claude.json").read_text() == '{"which": "state"}'


def test_prepare_creates_the_state_directory_when_it_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    ClaudeCodeHarness().prepare()

    assert (tmp_path / ".claude").is_dir()
