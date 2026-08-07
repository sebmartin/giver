import os

import pytest
from unittest.mock import MagicMock, patch

from giver.cli import (
    _PROJECT_ROOT,
    _container_name,
    _ensure_image,
    cancel,
    chat,
    run,
    shell,
)


def test_container_name_includes_stem():
    assert _container_name("my-workflow").startswith("giver-my-workflow-")


def _docker_calls(mock):
    return [c[0][0] for c in mock.call_args_list]


def _docker_run(mock, flag):
    """The `docker run` carrying `flag` — `-d` starts a workflow container,
    `-it` an interactive one. Picked by what it is rather than by position, so
    the provisioning calls around it don't shift an index."""
    return next(
        c for c in _docker_calls(mock) if c[:2] == ["docker", "run"] and flag in c
    )


def _mock_side_effects(volume_exists=True):
    """Answer each docker call by what it asks for, so a test is not coupled to
    how many calls a run makes."""

    def respond(cmd, *_, **__):
        if cmd[:3] == ["docker", "volume", "inspect"]:
            return MagicMock(returncode=0 if volume_exists else 1)
        if cmd[:2] == ["docker", "wait"]:
            return MagicMock(returncode=0, stdout="0\n")
        return MagicMock(returncode=0)

    return respond


# ── _ensure_image ─────────────────────────────────────────────────────────────


def test_ensure_image_skips_build_when_image_exists():
    with patch(
        "giver.cli.subprocess.run", return_value=MagicMock(returncode=0)
    ) as mock:
        _ensure_image()

    calls = _docker_calls(mock)
    assert ["docker", "image", "inspect", "giver:latest"] in calls
    assert not any("build" in c for c in calls)


def test_ensure_image_builds_from_package_root_not_cwd():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = [
            MagicMock(returncode=1),  # docker image inspect → not found
            MagicMock(returncode=0),  # docker info → daemon running
            MagicMock(returncode=0),  # docker build
        ]
        _ensure_image()

    build_cmd = _docker_calls(mock)[2]
    assert "build" in build_cmd
    assert str(_PROJECT_ROOT) in build_cmd
    assert "." not in build_cmd


def test_ensure_image_builds_only_once_across_calls():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = [
            MagicMock(returncode=1),  # 1st call: inspect → not found
            MagicMock(returncode=0),  # 1st call: docker info
            MagicMock(returncode=0),  # 1st call: build
            MagicMock(returncode=0),  # 2nd call: inspect → found
        ]
        _ensure_image()
        _ensure_image()

    build_calls = [c for c in _docker_calls(mock) if "build" in c]
    assert len(build_calls) == 1


def test_ensure_image_exits_cleanly_when_docker_not_running():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = [
            MagicMock(returncode=1),  # docker image inspect → not found
            MagicMock(returncode=1),  # docker info → daemon not running
        ]
        with pytest.raises(SystemExit) as exc:
            _ensure_image()
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


def test_run_runs_as_the_host_user(tmp_path):
    """Not root: a harness running unattended asks for no permission prompts,
    and claude-code refuses that combination. The host's own uid rather than a
    fixed one, because the run writes onto a bind mount whose files the host
    user has to own afterwards."""
    cmd = _start_cmd(_agent_workflow(tmp_path, "pi"), tmp_path)

    assert cmd[cmd.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert "HOME=/home/giver" in cmd  # a uid with no passwd entry has none


def test_run_creates_a_missing_state_volume_owned_by_the_host_user(tmp_path):
    """Docker creates a volume for a path the image does not carry as root, and
    the container is not root — so an unprovisioned volume is one the harness
    cannot write, and the login it holds could never have been saved."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects(volume_exists=False)
        run(_agent_workflow(tmp_path, "pi"), runs_dir=tmp_path / "runs")

    chown = next(c for c in _docker_calls(mock) if "chown" in c)
    assert chown == [
        "docker", "run", "--rm", "--user", "0",
        "-v", "giver-pi-state:/state",
        "--entrypoint", "chown", "giver:latest",
        f"{os.getuid()}:{os.getgid()}", "/state",
    ]


def test_run_leaves_an_existing_state_volume_alone(tmp_path):
    """Only the first use pays for provisioning — and re-chowning a volume
    would fight any ownership it has already settled on."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects(volume_exists=True)
        run(_agent_workflow(tmp_path, "pi"), runs_dir=tmp_path / "runs")

    assert not any("chown" in c for c in _docker_calls(mock))


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
    # Every value here is give'r's own or a harness's declared one; none is read
    # from the environment `giver` was invoked in.
    assert passed == {"HOME=/home/giver", "PI_OAUTH_CALLBACK_HOST=0.0.0.0"}


# ── shell ─────────────────────────────────────────────────────────────────────


def test_shell_pi_drops_into_bash_with_pi_volume():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("pi")

    cmd = _docker_run(mock, "-it")
    assert "--rm" in cmd and "-it" in cmd
    assert "53692:53692" in cmd
    assert "PI_OAUTH_CALLBACK_HOST=0.0.0.0" in cmd
    assert "giver-pi-state:/home/giver/.pi/agent" in cmd  # writable — login persists to the volume
    assert cmd[-9:] == [
        "--entrypoint", "python", "giver:latest",
        "-m", "giver.harness", "--harness", "pi", "--", "bash",
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
    tail = cmd[cmd.index("--entrypoint"):]
    assert tail == [
        "--entrypoint", "python", "giver:latest",
        "-m", "giver.harness", "--harness", "claude-code", "--", "bash",
    ]


def test_shell_no_harness_still_reaches_bash():
    """Nothing to prepare, same way in — one path, not two."""
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell()

    cmd = _docker_run(mock, "-it")
    assert cmd == [
        "docker", "run", "--rm", "-it",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", "HOME=/home/giver",
        "--entrypoint", "python", "giver:latest",
        "-m", "giver.harness", "--", "bash",
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
    assert cmd[cmd.index("--entrypoint"):] == [
        "--entrypoint", "python", "giver:latest",
        "-m", "giver.harness", "--harness", "pi", "--", "pi",
    ]


def test_chat_unknown_harness_returns_1():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)):
        assert chat("unknown") == 1


# ── cancel ────────────────────────────────────────────────────────────────────


def test_cancel_stops_named_container():
    with patch(
        "giver.cli.subprocess.run", return_value=MagicMock(returncode=0)
    ) as mock:
        cancel("giver-my-workflow-12345")

    assert mock.call_args[0][0] == ["docker", "stop", "giver-my-workflow-12345"]
