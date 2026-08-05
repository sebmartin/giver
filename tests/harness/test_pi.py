import logging
from unittest.mock import patch

from giver.harness import AgentStep, PiHarness

DONE = [{"type": "agent_end", "willRetry": False}]
RETRYING = [{"type": "agent_end", "willRetry": True}]


def session(session_id: str) -> dict:
    return {"type": "session", "version": 3, "id": session_id, "cwd": "/work/repo"}


def text(delta: str) -> dict:
    return {
        "type": "message_update",
        "assistantMessageEvent": {"type": "text_delta", "delta": delta},
    }


def step(prompt: str, model: str = "openai/gpt-5.5") -> AgentStep:
    return AgentStep(prompt=prompt, model=model)


async def run(steps, procs, log=None):
    with patch(
        "giver.harness.process.asyncio.create_subprocess_exec", side_effect=procs
    ) as spawn:
        result = await PiHarness().run(steps, log or logging.getLogger("n"))
    return result, spawn


async def test_passes_the_qualified_model_verbatim(mock_proc):
    """pi's --model accepts provider/id, so give'r's routing decision survives
    intact rather than being re-resolved by pi's fuzzy matcher."""
    result, spawn = await run([step("go", "anthropic/claude-opus-4-5")], [mock_proc(DONE)])

    assert result == 0
    cmd = spawn.call_args_list[0][0]
    assert cmd[cmd.index("--model") + 1] == "anthropic/claude-opus-4-5"


async def test_writes_the_prompt_to_stdin(mock_proc):
    proc = mock_proc(DONE)
    await run([step("say hello")], [proc])

    written = b"".join(c[0][0] for c in proc.stdin.write.call_args_list)
    assert written == b"say hello"


async def test_streams_text_to_the_log(mock_proc, caplog):
    with caplog.at_level(logging.INFO, logger="mynode"):
        await run(
            [step("hi")],
            [mock_proc([text("hello there")] + DONE)],
            logging.getLogger("mynode"),
        )
    assert "hello there" in caplog.text


async def test_forks_the_session_from_the_first_step(mock_proc):
    """Fork rather than continue in place, so a replayed step can't mutate the
    session it resumed from."""
    procs = [mock_proc([session("sess-abc")] + DONE), mock_proc(DONE)]
    _, spawn = await run([step("p1"), step("p2")], procs)

    first, second = spawn.call_args_list[0][0], spawn.call_args_list[1][0]
    assert "--fork" not in first
    assert second[second.index("--fork") + 1] == "sess-abc"


async def test_switches_model_per_step(mock_proc):
    procs = [mock_proc([session("s")] + DONE), mock_proc(DONE)]
    _, spawn = await run(
        [step("p1", "openai/gpt-5.5"), step("p2", "openai/o3-mini")], procs
    )

    first, second = spawn.call_args_list[0][0], spawn.call_args_list[1][0]
    assert first[first.index("--model") + 1] == "openai/gpt-5.5"
    assert second[second.index("--model") + 1] == "openai/o3-mini"


async def test_stops_after_a_failed_step(mock_proc):
    result, spawn = await run([step("p1"), step("p2")], [mock_proc(RETRYING)])

    assert result == 1
    assert spawn.call_count == 1  # a step that never ran is a process never spawned


async def test_fails_when_the_stream_ends_without_agent_end(mock_proc):
    result, _ = await run([step("p")], [mock_proc([])])
    assert result == 1


async def test_fails_on_nonzero_exit_despite_a_clean_stream(mock_proc):
    """Process exit and in-band status are independent channels — a harness that
    reports success and then dies has still failed."""
    result, _ = await run([step("p")], [mock_proc(DONE, exit_code=1)])
    assert result == 1


async def test_fails_loudly_when_a_later_step_has_no_session_to_fork(mock_proc, caplog):
    """Silently running step 2 unforked would lose continuity without a trace."""
    procs = [mock_proc(DONE), mock_proc(DONE)]
    with caplog.at_level(logging.ERROR):
        result, spawn = await run([step("p1"), step("p2")], procs)

    assert result == 1
    assert spawn.call_count == 1
    assert "no session id" in caplog.text
