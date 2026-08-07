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
from giver.image import source_fingerprint


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


CURRENT = source_fingerprint(_PROJECT_ROOT)


def _mock_side_effects(image_source: str | None = CURRENT, exit_code: str = "0"):
    """Answer each docker call by what it asks for, so a test is not coupled to
    how many calls a run makes.

    `image_source` is the `giver.source` label on the image being asked about;
    None means there is no such image. It defaults to the source actually in
    the tree, so a test about something else never triggers a build.
    """

    def respond(cmd, *_, **__):
        if cmd[:3] == ["docker", "image", "inspect"]:
            if image_source is None:
                return MagicMock(returncode=1, stdout="")
            return MagicMock(returncode=0, stdout=f"{image_source}\n")
        if cmd[:2] == ["docker", "wait"]:
            return MagicMock(returncode=0, stdout=f"{exit_code}\n")
        return MagicMock(returncode=0)

    return respond


# ── _ensure_image ─────────────────────────────────────────────────────────────


def test_ensure_image_skips_build_when_the_image_holds_this_source():
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        image = _ensure_image([PiHarness()])

    assert image == "giver:dev-pi"
    assert not any("build" in c for c in _docker_calls(mock))


def test_ensure_image_rebuilds_when_the_source_changed():
    """A version does not move while someone is editing, so it cannot answer
    "is the give'r in this image the give'r I am running". Without this you run
    yesterday's kernel against today's workflow and nothing says so."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects("stale0000000")
        _ensure_image([PiHarness()])

    assert _build(mock)[0][0][:5] == ["docker", "build", "-t", "giver:dev-pi", "-f"]


def test_ensure_image_gives_each_harness_set_its_own_tag():
    """Not one image that grows. Contents would then be a function of what this
    machine happens to have run, so two people running the same workflow get
    different images — each a combination of harnesses nobody chose or tested."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects()
        tags = [
            _ensure_image([PiHarness()]),
            _ensure_image([CodexHarness()]),
            _ensure_image([PiHarness(), CodexHarness()]),
            _ensure_image(),
        ]

    assert tags == ["giver:dev-pi", "giver:dev-codex", "giver:dev-codex_pi", "giver:dev-base"]


def test_ensure_image_feeds_the_dockerfile_in_rather_than_reading_one():
    """There is no Dockerfile on disk to build. `-f -` takes the file from
    stdin while still taking a context from the path — `docker build -` would
    send the context itself and leave the COPY lines nothing to copy."""
    with patch("giver.cli.subprocess.run") as mock:
        mock.side_effect = _mock_side_effects(None)
        _ensure_image([PiHarness()])

    argv, kwargs = _build(mock)[0][0], _build(mock)[1]
    assert argv == ["docker", "build", "-t", "giver:dev-pi", "-f", "-", str(_PROJECT_ROOT)]
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
    assert "giver:dev-base" in start_cmd


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
        mock.side_effect = _mock_side_effects(exit_code="1")
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
        "giver:dev-pi",
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
    tail = cmd[cmd.index("giver:dev-claude-code"):]
    assert tail == [
        "giver:dev-claude-code",
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
        "giver:dev-base",
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
    assert cmd[cmd.index("giver:dev-pi"):] == [
        "giver:dev-pi",
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
