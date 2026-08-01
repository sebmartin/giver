import logging
from unittest.mock import patch

from giver.harness import AgentStep, CodexHarness


def thread_started(thread_id: str) -> dict:
    return {"type": "thread.started", "thread_id": thread_id}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


def item_text(text: str) -> dict:
    return {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}}


def step(prompt: str, model: str = "openai/gpt-5.5") -> AgentStep:
    return AgentStep(prompt=prompt, model=model)


async def run(steps, procs, log=None):
    with patch("giver.harness.codex.asyncio.create_subprocess_exec", side_effect=procs) as spawn:
        result = await CodexHarness().run(steps, log or logging.getLogger("n"))
    return result, spawn


async def test_headless_is_a_subcommand_not_a_flag(mock_proc):
    """codex exec, where pi and claude take -p — the Protocol builds argv freely
    rather than assuming a base command plus flags."""
    _, spawn = await run([step("go")], [mock_proc([thread_started("t1")])])

    cmd = list(spawn.call_args_list[0][0])
    assert cmd[:2] == ["codex", "exec"]
    assert cmd[-1] == "-"  # prompt on stdin
    assert cmd[cmd.index("--model") + 1] == "gpt-5.5"  # bare name, vendor stripped


async def test_resumes_by_thread_id(mock_proc):
    """codex calls sessions threads: the id arrives as thread_id on
    thread.started, and resume continues that thread in place."""
    procs = [mock_proc([thread_started("thr-abc")]), mock_proc([thread_started("thr-abc")])]
    _, spawn = await run([step("p1"), step("p2")], procs)

    first, second = list(spawn.call_args_list[0][0]), list(spawn.call_args_list[1][0])
    assert "resume" not in first
    assert second[:3] == ["codex", "exec", "resume"]
    assert "thr-abc" in second


def test_declares_that_it_cannot_fork():
    """Headless codex has no forking resume, so replaying a step mutates the
    thread it resumed from. Anything that replays work must read this rather
    than assume every harness behaves like pi."""
    assert CodexHarness().forks_on_resume is False


async def test_fails_on_an_in_band_error_event(mock_proc):
    result, _ = await run([step("go")], [mock_proc([thread_started("t1"), error("boom")])])
    assert result == 1


async def test_fails_on_nonzero_exit_despite_a_clean_stream(mock_proc):
    result, _ = await run([step("go")], [mock_proc([thread_started("t1")], exit_code=1)])
    assert result == 1


async def test_logs_item_text(mock_proc, caplog):
    with caplog.at_level(logging.INFO):
        result, _ = await run(
            [step("go")], [mock_proc([thread_started("t1"), item_text("all done")])]
        )

    assert result == 0
    assert "all done" in caplog.text


async def test_ignores_non_json_log_lines(mock_proc):
    """codex interleaves plain-text tracing with its JSONL."""
    proc = mock_proc([thread_started("t1")])
    result, _ = await run([step("go")], [proc])
    assert result == 0
