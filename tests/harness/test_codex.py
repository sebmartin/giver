import logging
from unittest.mock import patch

from giver.harness import AgentStep, CodexHarness

# Event shapes copied from a real `codex exec --json` run (codex-cli 0.146.0).
DONE = [{"type": "turn.completed", "usage": {"input_tokens": 13632, "output_tokens": 5}}]


def thread_started(thread_id: str) -> dict:
    return {"type": "thread.started", "thread_id": thread_id}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


def turn_failed(message: str) -> dict:
    return {"type": "turn.failed", "error": {"message": message}}


def item_error(message: str) -> dict:
    return {"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": message}}


def item_text(text: str) -> dict:
    return {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}}


def step(prompt: str, model: str = "openai/gpt-5.5") -> AgentStep:
    return AgentStep(prompt=prompt, model=model)


async def run(steps, procs, log=None):
    with patch(
        "giver.harness.process.asyncio.create_subprocess_exec", side_effect=procs
    ) as spawn:
        result = await CodexHarness().run(steps, log or logging.getLogger("n"))
    return result, spawn


async def test_headless_is_a_subcommand_not_a_flag(mock_proc):
    """codex exec, where pi and claude take -p — the Protocol builds argv freely
    rather than assuming a base command plus flags."""
    _, spawn = await run([step("go")], [mock_proc([thread_started("t1")] + DONE)])

    assert list(spawn.call_args_list[0][0]) == [
        "codex",
        "exec",
        "--json",
        "--model", "gpt-5.5",  # bare name, vendor stripped
        "--dangerously-bypass-approvals-and-sandbox",
        "-",  # prompt on stdin
    ]


async def test_writes_the_prompt_to_stdin(mock_proc):
    """The `-` sentinel is the whole input path: nothing reaches the agent if
    the write, the encode, or the close is lost."""
    proc = mock_proc([thread_started("t1")] + DONE)
    await run([step("say hello")], [proc])

    written = b"".join(c[0][0] for c in proc.stdin.write.call_args_list)
    assert written == b"say hello"
    assert proc.stdin.close.called  # real `codex exec -` blocks until EOF


async def test_resumes_by_thread_id(mock_proc):
    """codex calls sessions threads: the id arrives as thread_id on
    thread.started, and `resume` continues that thread in place — a real resume
    re-emits the same id rather than minting one. The id is a bare positional
    before the `-` sentinel; `codex exec resume [SESSION_ID] [PROMPT]` would
    otherwise read it as the prompt and silently start a new thread."""
    resumed = [thread_started("thr-abc")] + DONE  # same id back, not a new one
    procs = [mock_proc([thread_started("thr-abc")] + DONE), mock_proc(resumed)]
    _, spawn = await run([step("p1"), step("p2")], procs)

    first, second = list(spawn.call_args_list[0][0]), list(spawn.call_args_list[1][0])
    assert "resume" not in first
    assert second == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--model", "gpt-5.5",
        "--dangerously-bypass-approvals-and-sandbox",
        "thr-abc",
        "-",
    ]


def test_declares_that_it_cannot_fork():
    """Headless codex has no forking resume, so replaying a step mutates the
    thread it resumed from. Anything that replays work must read this rather
    than assume every harness behaves like pi."""
    assert CodexHarness().forks_on_resume is False


async def test_fails_on_an_in_band_error_event(mock_proc, caplog):
    with caplog.at_level(logging.ERROR):
        result, _ = await run([step("go")], [mock_proc([thread_started("t1"), error("boom")])])

    assert result == 1
    assert "boom" in caplog.text


async def test_fails_on_turn_failed(mock_proc, caplog):
    """turn.failed nests its text under `error` where a top-level error event
    carries it flat; both have to reach the log."""
    proc = mock_proc([thread_started("t1"), turn_failed("nope")])
    with caplog.at_level(logging.ERROR):
        result, _ = await run([step("go")], [proc])

    assert result == 1
    assert "nope" in caplog.text


async def test_an_item_level_error_is_advisory_not_a_verdict(mock_proc, caplog):
    """codex emits an `error` item for soft conditions — unrecognised model
    metadata, say — and then carries on to complete the turn. Only turn-level
    events decide, or a benign warning would fail an otherwise good run."""
    events = [thread_started("t1"), item_error("Defaulting to fallback metadata")] + DONE
    with caplog.at_level(logging.ERROR):
        result, _ = await run([step("go")], [mock_proc(events)])

    assert result == 0
    assert "fallback metadata" in caplog.text  # logged, just not fatal


async def test_fails_when_the_stream_ends_without_a_completed_turn(mock_proc, caplog):
    """A truncated stream, or a terminal event under a name this code doesn't
    know, must not read as success — give'r skips a node whose artifact exists,
    so a run wrongly recorded as successful can never be regenerated."""
    with caplog.at_level(logging.ERROR):
        result, _ = await run([step("go")], [mock_proc([thread_started("t1"), item_text("half")])])

    assert result == 1
    assert "without a turn.completed" in caplog.text


async def test_fails_on_nonzero_exit_despite_a_clean_stream(mock_proc):
    result, _ = await run([step("go")], [mock_proc([thread_started("t1")] + DONE, exit_code=1)])
    assert result == 1


async def test_stops_after_a_failed_step(mock_proc):
    """codex resumes in place, so step 2 would mutate the very thread that just
    failed — with no branch to fall back to."""
    procs = [mock_proc([thread_started("t1"), error("boom")]), mock_proc(DONE)]
    result, spawn = await run([step("p1"), step("p2")], procs)

    assert result == 1
    assert spawn.call_count == 1  # a step that never ran is a process never spawned


async def test_logs_item_text(mock_proc, caplog):
    with caplog.at_level(logging.INFO):
        result, _ = await run(
            [step("go")], [mock_proc([thread_started("t1"), item_text("all done")] + DONE)]
        )

    assert result == 0
    assert "all done" in caplog.text


async def test_ignores_non_json_log_lines(mock_proc, caplog):
    """Defensive: every line observed off a real run is JSONL, but a stray
    plain-text one must not abort the stream — parsing has to continue past it,
    not merely survive it."""
    events = ["2026-01-01T00:00:00 INFO booting", thread_started("t1"), item_text("done")] + DONE
    with caplog.at_level(logging.INFO):
        result, _ = await run([step("go")], [mock_proc(events)])

    assert result == 0
    assert "done" in caplog.text


async def test_fails_loudly_when_a_later_step_has_no_thread_to_resume(mock_proc, caplog):
    """Running step 2 without the id would start a fresh, contextless thread and
    still report success."""
    procs = [mock_proc(DONE), mock_proc(DONE)]
    with caplog.at_level(logging.ERROR):
        result, spawn = await run([step("p1"), step("p2")], procs)

    assert result == 1
    assert spawn.call_count == 1
    assert "no thread id" in caplog.text


async def test_a_lone_step_needs_no_thread_id(mock_proc):
    """The guard is about continuity, so it must not fire on the last step."""
    result, _ = await run([step("only")], [mock_proc(DONE)])
    assert result == 0
