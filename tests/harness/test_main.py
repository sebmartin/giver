"""The `python -m giver.harness` shim.

`giver shell` and `giver chat` hand a container straight to bash or a harness's
REPL. This is the only give'r code that runs on those paths, and they are the
paths a first-pass login goes through — so a harness that is not prepared here
is one whose login is written where it will not survive the container.
"""

from unittest.mock import patch

import pytest

from giver.harness.__main__ import main


def _run(argv: list[str]):
    with patch("giver.harness.__main__.os.execvp") as execvp:
        with patch("giver.harness.__main__.harness_by_name") as by_name:
            main(argv)
    return execvp, by_name


def test_prepares_the_named_harness_then_execs_the_command():
    execvp, by_name = _run(["--harness", "claude-code", "--", "bash"])

    by_name.assert_called_once_with("claude-code")
    by_name.return_value.prepare.assert_called_once_with()
    assert execvp.call_args[0] == ("bash", ["bash"])


def test_prepares_every_named_harness():
    _, by_name = _run(["--harness", "pi", "--harness", "claude-code", "--", "bash"])

    assert [c[0][0] for c in by_name.call_args_list] == ["pi", "claude-code"]


def test_execs_a_multi_word_command_intact():
    """A harness's `repl_cmd` is argv, not a word — it has to survive as one."""
    execvp, _ = _run(["--harness", "codex", "--", "codex", "--search"])

    assert execvp.call_args[0] == ("codex", ["codex", "--search"])


def test_runs_the_command_when_no_harness_is_named():
    """Bare `giver shell` has nothing to prepare and still has to reach bash."""
    execvp, by_name = _run(["--", "bash"])

    by_name.assert_not_called()
    assert execvp.call_args[0] == ("bash", ["bash"])


def test_prepare_failure_stops_short_of_exec():
    """Handing over a container whose harness could not be arranged would put
    the user in front of a login prompt that silently writes nowhere."""
    with patch("giver.harness.__main__.os.execvp") as execvp:
        with patch("giver.harness.__main__.harness_by_name", side_effect=ValueError("unknown harness 'nope'")):
            with pytest.raises(SystemExit) as exc:
                main(["--harness", "nope", "--", "bash"])

    assert exc.value.code == 1
    execvp.assert_not_called()


def test_no_command_is_an_error():
    with pytest.raises(SystemExit):
        main(["--harness", "pi"])
