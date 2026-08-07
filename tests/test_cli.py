import os

import pytest
from unittest.mock import MagicMock, patch

from giver.cli import (
    _PROJECT_ROOT,
    _container_name,
    _ensure_image,
    cancel,
    chat,
    dockerfile,
    run,
    shell,
)
from giver.harness import CodexHarness, PiHarness


def test_container_name_includes_stem():
    assert _container_name("my-workflow").startswith("giver-my-workflow-")


def _docker_calls(mock):
    return [c[0][0] for c in mock.call_args_list]


def _docker_run(mock, flag):
    """The `docker run` carrying `flag` — `-d` starts a workflow container,
    `-it` an interactive one. Picked by what it is rather than by position, so
    the calls around it don't shift an index."""
    return next(
        c for c in _docker_calls(mock) if c[:2] == ["docker", "run"] and flag in c
    )


def _build(mock):
    """The `docker build` call — argv at [0][0], kwargs at [1]."""
    return next(c for c in mock.call_args_list if "build" in c[0][0])


def _mock_side_effects(image_harnesses="claude-code,codex,pi"):
    """Answer each docker call by what it asks for, so a test is not coupled to
    how many calls a run makes.

    `image_harnesses` is the label on the local image; None means there is no
    local image. The default carries everything, so a test about something else
    never accidentally triggers a build.
    """

    def respond(cmd, *_, **__):
        if cmd[:3] == ["docker", "image", "inspect"]:
            if image_harnesses is None:
                return MagicMock(returncode=1, stdout="")
            return MagicMock(returncode=0, stdout=f"{image_harnesses}\n")
        if cmd[:2] == ["docker", "wait"]:
            return MagicMock(returncode=0, stdout="0\n")
        return MagicMock(returncode=0)

    return respond


# ── _ensure_image ─────────────────────────────────────────────────────────────


def test_ensure_image_skips_build_when_the_image_already_carries_them():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects("claude-code,pi")
        _ensure_image([PiHarness()])

    assert not any("build" in c for c in _docker_calls(mock))


def test_ensure_image_builds_when_a_named_harness_is_missing():
    """Build-if-stale, not build-if-absent: a workflow that starts naming a
    harness the image predates would otherwise die on a missing binary partway
    through the run."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects("pi")
        _ensure_image([CodexHarness()])

    assert 'LABEL giver.harnesses="codex,pi"' in _build(mock)[1]["input"]


def test_ensure_image_keeps_what_the_image_already_carried():
    """One tag serves every workflow on this machine. Rebuilding to exactly
    what this run needs would drop what the last one needed, and alternating
    between two workflows would rebuild on every invocation."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects("claude-code")
        _ensure_image([PiHarness()])

    assert 'LABEL giver.harnesses="claude-code,pi"' in _build(mock)[1]["input"]


def test_ensure_image_feeds_the_dockerfile_in_rather_than_reading_one():
    """There is no Dockerfile on disk to build. `-f -` takes the file from
    stdin while still taking a context from the path — `docker build -` would
    send the context itself and leave the COPY lines nothing to copy."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects(None)
        _ensure_image([PiHarness()])

    argv, kwargs = _build(mock)[0][0], _build(mock)[1]
    assert argv == ["docker", "build", "-t", "giver:latest", "-f", "-", str(_PROJECT_ROOT)]
    assert kwargs["input"].startswith("FROM python:3.13-slim")


def test_ensure_image_exits_cleanly_when_docker_not_running():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = [
            MagicMock(returncode=1),  # docker image inspect → no image
            MagicMock(returncode=1),  # docker info → daemon not running
        ]
        with pytest.raises(SystemExit) as exc:
            _ensure_image([PiHarness()])
    assert exc.value.code == 1


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_starts_detached_named_container(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")
    runs_dir = tmp_path / "runs"

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=runs_dir)

    start_cmd = _docker_run(mock, "-d")
    assert "-d" in start_cmd
    assert f"{wf.resolve()}:/workflow.yaml:ro" in start_cmd
    assert f"{runs_dir}:/runs" in start_cmd
    assert "giver:latest" in start_cmd


def test_run_streams_logs_then_waits(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=tmp_path / "runs")

    calls = _docker_calls(mock)
    assert any("logs" in c for c in calls)
    assert any("wait" in c for c in calls)


def test_run_detach_skips_streaming(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")

    with patch(
        "giver.cli.subprocess.run", return_value=MagicMock(returncode=0)
    ) as mock:
        run(wf, runs_dir=tmp_path / "runs", detach=True)

    assert not any("logs" in c for c in _docker_calls(mock))


def test_run_returns_workflow_exit_code(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = [
            MagicMock(returncode=0),  # docker image inspect
            MagicMock(returncode=0),  # docker run -d
            MagicMock(returncode=0),  # docker logs -f
            MagicMock(returncode=0, stdout="1\n"),  # docker wait → exit 1
        ]
        assert run(wf, runs_dir=tmp_path / "runs") == 1


def test_run_writes_runs_log(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")
    runs_dir = tmp_path / "runs"

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=runs_dir)

    text = (runs_dir / "runs.log").read_text()
    assert "start" in text
    assert "exit 0" in text


def _agent_workflow(tmp_path, *harnesses: str):
    """A workflow with one agent node per named harness."""
    nodes = "\n".join(
        f"  - name: n{i}\n"
        f"    type: agent\n"
        f"    harness: {h}\n"
        f"    model: {'anthropic/claude-haiku-4-5' if h == 'claude-code' else 'openai/gpt-5.5'}\n"
        f"    steps:\n"
        f"      - prompt: go"
        for i, h in enumerate(harnesses)
    )
    wf = tmp_path / "workflow.yaml"
    wf.write_text(f"name: test\nnodes:\n{nodes}\n")
    return wf


def _start_cmd(wf, tmp_path):
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=tmp_path / "runs")
    return _docker_run(mock, "-d")


def test_run_mounts_harness_state_volumes_writable(tmp_path):
    """Writable, not read-only: the harnesses write session transcripts and
    refreshed tokens into these directories during a run, so a read-only mount
    breaks multi-step nodes, resume, and unattended auth alike."""
    cmd = _start_cmd(_agent_workflow(tmp_path, "pi", "claude-code"), tmp_path)

    assert "giver-pi-state:/home/giver/.pi/agent" in cmd
    assert "giver-claude-code-state:/home/giver/.claude" in cmd
    assert not any(c.endswith(":ro") and "-state" in c for c in cmd)


def test_run_mounts_only_the_harnesses_the_workflow_uses(tmp_path):
    cmd = _start_cmd(_agent_workflow(tmp_path, "pi"), tmp_path)

    assert "giver-pi-state:/home/giver/.pi/agent" in cmd
    assert not any("claude-code-state" in c for c in cmd)


def test_run_mounts_nothing_for_a_workflow_with_no_agent_nodes(tmp_path):
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes:\n  - name: b\n    type: bash\n    command: 'true'\n")

    cmd = _start_cmd(wf, tmp_path)
    assert not any("-state" in c for c in cmd)


def test_run_applies_harness_environment_but_publishes_no_ports(tmp_path):
    """A run needs pi's environment, but nothing is there to complete an OAuth
    callback — logging in happens beforehand via `giver shell`."""
    cmd = _start_cmd(_agent_workflow(tmp_path, "pi"), tmp_path)

    assert "PI_OAUTH_CALLBACK_HOST=0.0.0.0" in cmd
    assert "-p" not in cmd


def test_run_forwards_no_credentials_from_the_host_environment(tmp_path):
    """give'r's credentials come from a login inside its own environment; the
    host's are never read implicitly."""
    cmd = _start_cmd(_agent_workflow(tmp_path, "pi", "claude-code"), tmp_path)

    passed = {c for i, c in enumerate(cmd) if i and cmd[i - 1] == "-e"}
    assert passed == {
        "PI_OAUTH_CALLBACK_HOST=0.0.0.0",
        f"GIVER_UID={os.getuid()}",
        f"GIVER_GID={os.getgid()}",
    }


# ── shell ─────────────────────────────────────────────────────────────────────


def test_shell_pi_drops_into_bash_with_pi_volume():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("pi")

    cmd = _docker_run(mock, "-it")
    assert "--rm" in cmd and "-it" in cmd
    assert "53692:53692" in cmd
    assert "PI_OAUTH_CALLBACK_HOST=0.0.0.0" in cmd
    assert "giver-pi-state:/home/giver/.pi/agent" in cmd  # writable — login persists to the volume
    assert cmd[-8:] == [
        "giver:latest",
        "python", "-m", "giver.harness", "--harness", "pi", "--", "bash",
    ]


def test_shell_claude_drops_into_bash_with_claude_volume():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("claude-code")

    cmd = _docker_run(mock, "-it")
    assert "giver-claude-code-state:/home/giver/.claude" in cmd
    assert cmd[-1] == "bash"


def test_shell_enters_through_giver_so_the_harness_is_prepared(tmp_path):
    """The state volume is only half of provisioning: claude keeps its
    config outside the directory give'r mounts, and `giver shell` is the
    first-pass login path — the one place it must already be reconciled."""
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("claude-code")

    cmd = _docker_run(mock, "-it")
    tail = cmd[cmd.index("giver:latest"):]
    assert tail == [
        "giver:latest",
        "python", "-m", "giver.harness", "--harness", "claude-code", "--", "bash",
    ]


def test_shell_no_harness_still_reaches_bash():
    """Nothing to prepare, same way in — one path, not two."""
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell()

    cmd = _docker_run(mock, "-it")
    assert cmd == [
        "docker", "run", "--rm", "-it",
        "-e", f"GIVER_UID={os.getuid()}", "-e", f"GIVER_GID={os.getgid()}",
        "giver:latest",
        "python", "-m", "giver.harness", "--", "bash",
    ]


def test_shell_unknown_harness_returns_1():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)):
        assert shell("unknown") == 1


# ── chat ──────────────────────────────────────────────────────────────────────


def test_chat_launches_the_harness_repl_with_the_same_provisioning():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        chat("pi")

    cmd = _docker_run(mock, "-it")
    assert "53692:53692" in cmd  # same declared infra as `shell pi`
    assert "giver-pi-state:/home/giver/.pi/agent" in cmd
    assert cmd[cmd.index("giver:latest"):] == [
        "giver:latest",
        "python", "-m", "giver.harness", "--harness", "pi", "--", "pi",
    ]


def test_chat_unknown_harness_returns_1():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)):
        assert chat("unknown") == 1


# ── dockerfile ────────────────────────────────────────────────────────────────


def test_dockerfile_carries_only_the_harnesses_the_workflows_name(tmp_path, capsys):
    wf = _agent_workflow(tmp_path, "pi")

    assert dockerfile([wf], dev=_PROJECT_ROOT) == 0

    out = capsys.readouterr().out
    assert 'LABEL giver.harnesses="pi"' in out
    assert "@anthropic-ai/claude-code" not in out


def test_dockerfile_unions_the_harnesses_across_workflows(tmp_path, capsys):
    """An image is built for a set of workflows, not for one — CI has several
    and wants one runtime that runs all of them."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    pi_wf = _agent_workflow(a, "pi")
    claude_wf = _agent_workflow(b, "claude-code")

    assert dockerfile([pi_wf, claude_wf], dev=_PROJECT_ROOT) == 0

    assert 'LABEL giver.harnesses="claude-code,pi"' in capsys.readouterr().out


def test_dockerfile_without_a_workflow_carries_every_harness(capsys):
    """Nothing to derive a set from, so emit the runtime that runs anything."""
    assert dockerfile(dev=_PROJECT_ROOT) == 0

    assert 'LABEL giver.harnesses="claude-code,codex,pi"' in capsys.readouterr().out


def test_dockerfile_without_dev_fails_rather_than_reaching_for_pypi(capsys):
    """`giver` is an unregistered PyPI name; a generated file that installed it
    would run whoever claims it inside the credential container."""
    assert dockerfile(dev=None) == 1

    assert "not published to PyPI" in capsys.readouterr().err


# ── cancel ────────────────────────────────────────────────────────────────────


def test_cancel_stops_named_container():
    with patch(
        "giver.cli.subprocess.run", return_value=MagicMock(returncode=0)
    ) as mock:
        cancel("giver-my-workflow-12345")

    assert mock.call_args[0][0] == ["docker", "stop", "giver-my-workflow-12345"]
