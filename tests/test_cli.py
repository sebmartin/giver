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


def _mock_side_effects():
    return [
        MagicMock(returncode=0),  # docker image inspect (image exists)
        MagicMock(returncode=0),  # docker run -d
        MagicMock(returncode=0),  # docker logs -f
        MagicMock(returncode=0, stdout="0\n"),  # docker wait
    ]


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

    start_cmd = _docker_calls(mock)[1]  # index 1: after inspect
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


def test_run_mounts_harness_credential_volumes_writable(tmp_path):
    """Writable, not read-only: the harnesses write session transcripts and
    refreshed tokens into these directories during a run, so a read-only mount
    breaks multi-step nodes, resume, and unattended auth alike."""
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=tmp_path / "runs")

    start_cmd = _docker_calls(mock)[1]
    assert "giver-pi-creds:/root/.pi/agent" in start_cmd
    assert "giver-claude-creds:/root/.claude" in start_cmd
    assert not any(c.endswith("-creds:/root/.pi/agent:ro") for c in start_cmd)


def test_run_forwards_no_credentials_from_the_host_environment(tmp_path):
    """give'r's credentials exist only via a login run inside its own
    environment — nothing is read implicitly from the host."""
    wf = tmp_path / "workflow.yaml"
    wf.write_text("name: test\nnodes: []")

    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        run(wf, runs_dir=tmp_path / "runs")

    start_cmd = _docker_calls(mock)[1]
    assert "-e" not in start_cmd


# ── shell ─────────────────────────────────────────────────────────────────────


def test_shell_pi_drops_into_bash_with_pi_volume():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("pi")

    cmd = _docker_calls(mock)[1]
    assert "--rm" in cmd and "-it" in cmd
    assert "53692:53692" in cmd
    assert "PI_OAUTH_CALLBACK_HOST=0.0.0.0" in cmd
    assert "giver-pi-creds:/root/.pi/agent" in cmd  # writable — login persists to the volume
    assert cmd[-3:] == ["--entrypoint", "bash", "giver:latest"]


def test_shell_claude_drops_into_bash_with_claude_volume():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell("claude")

    cmd = _docker_calls(mock)[1]
    assert "giver-claude-creds:/root/.claude" in cmd
    assert cmd[-3:] == ["--entrypoint", "bash", "giver:latest"]


def test_shell_no_harness_is_bare_bash():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        shell()

    cmd = _docker_calls(mock)[1]
    assert cmd == ["docker", "run", "--rm", "-it", "--entrypoint", "bash", "giver:latest"]


def test_shell_unknown_harness_returns_1():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)):
        assert shell("unknown") == 1


# ── chat ──────────────────────────────────────────────────────────────────────


def test_chat_launches_the_harness_repl_with_the_same_provisioning():
    with patch("giver.cli.subprocess.run", return_value=MagicMock(returncode=0)) as mock:
        chat("pi")

    cmd = _docker_calls(mock)[1]
    assert "53692:53692" in cmd  # same declared infra as `shell pi`
    assert "giver-pi-creds:/root/.pi/agent" in cmd
    assert cmd[-3:] == ["--entrypoint", "pi", "giver:latest"]


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
