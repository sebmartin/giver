import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from giver.harness import HARNESS_NAMES, Harness, harness_by_name
from giver.kernel.nodes.agent import AgentNode
from giver.kernel.workflow import Workflow


def _container_name(workflow_stem: str) -> str:
    return f"giver-{workflow_stem}-{int(time.time())}"


_PROJECT_ROOT = Path(__file__).parents[2]


def _ensure_image() -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", "giver:latest"],
        capture_output=True,
    )
    if result.returncode == 0:
        return
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("error: Docker is not running", file=sys.stderr)
        sys.exit(1)
    result = subprocess.run(
        ["docker", "build", "-t", "giver:latest", str(_PROJECT_ROOT)],
    )
    if result.returncode != 0:
        print("error: image build failed", file=sys.stderr)
        sys.exit(1)


# The container runs as an unprivileged user, so `~` is this and not `/root`.
# A harness running unattended asks for no permission prompts, and root plus no
# prompts is a wider blast radius than either alone — claude-code refuses the
# combination outright.
_CONTAINER_HOME = "/home/giver"


def _user_args() -> list[str]:
    """Run the container as the host user who invoked give'r.

    A run writes its logs and artifacts onto a bind mount, and on a Linux host
    the uid crosses the boundary unchanged — so whatever the container creates
    has to already belong to the person who will read it afterwards. Taking the
    host's uid also makes the state volumes stable across runs on that machine.

    `HOME` is passed because a uid with no `/etc/passwd` entry has no home
    directory to look up, and every path give'r derives hangs off `~`.
    """
    return [
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-e", f"HOME={_CONTAINER_HOME}",
    ]


# A harness describes itself in its own terms — pi says `~/.pi/agent`. These
# translate that into the Docker names for the container, which is knowledge the
# host has and the harness shouldn't.
def _volume(harness: Harness) -> str:
    # `state`, not `creds`: it is the harness's whole `state_path` — sessions,
    # config and project history as much as the token.
    return f"giver-{harness.name}-state"


def _container_path(harness: Harness) -> str:
    return f"{_CONTAINER_HOME}/" + harness.state_path.removeprefix("~/")


def _ensure_state_volume(harness: Harness) -> None:
    """Create a harness's state volume owned by the user that will use it.

    Docker creates a volume for a path the image does not carry as root, and
    the container is not root — so a volume left to appear on its own is one
    the harness cannot write, and a login through it is lost when the container
    exits. Only the first use pays for this; after that the volume exists.

    Ownership is decided here for the same reason the volume's name and mount
    point are: it is a fact about how give'r runs a container, not about the
    harness, and the uid is not known until someone runs `giver`.
    """
    volume = _volume(harness)
    exists = subprocess.run(["docker", "volume", "inspect", volume], capture_output=True)
    if exists.returncode == 0:
        return
    created = subprocess.run(
        [
            "docker", "run", "--rm", "--user", "0",
            "-v", f"{volume}:/state",
            "--entrypoint", "chown", "giver:latest",
            f"{os.getuid()}:{os.getgid()}", "/state",
        ],
        capture_output=True,
    )
    if created.returncode != 0:
        print(
            f"error: could not create state volume {volume}: "
            f"{created.stderr.decode().strip()}",
            file=sys.stderr,
        )
        sys.exit(1)


def _harness_args(harness: Harness, interactive: bool) -> list[str]:
    """Docker arguments a harness needs: its environment, its state volume, and
    — only when someone will interact with it — the ports its login flow listens
    on.

    A run publishes no ports: logging in happens beforehand via `giver shell`,
    and a headless step has nothing to answer an OAuth callback with.
    """
    args = []
    for key, value in harness.env.items():
        args += ["-e", f"{key}={value}"]
    if interactive:
        for port in harness.ports:
            args += ["-p", port]
    return args + ["-v", f"{_volume(harness)}:{_container_path(harness)}"]


def _harnesses_for(workflow_abs: Path) -> list[Harness]:
    """The harnesses a workflow's agent nodes actually name.

    Parsing the workflow host-side means an unknown harness or an unresolvable
    model is reported here, before a container exists, using the same code the
    kernel will run inside it.
    """
    workflow = Workflow.from_file(workflow_abs)
    named = {n.harness_name for n in workflow.nodes if isinstance(n, AgentNode)}
    return [harness_by_name(name) for name in sorted(named)]


def _run_container(workflow_abs: Path, runs_dir: Path, name: str) -> None:
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        *_user_args(),
        "-v", f"{workflow_abs}:/workflow.yaml:ro",
        "-v", f"{runs_dir}:/runs",
    ]
    # Only what this workflow uses: an agent that goes wrong, or gets talked
    # into it, can read every credential in the container, so a pi workflow has
    # no reason to be holding Claude Code's token. Mounted writable — harnesses
    # write session transcripts as they work and rewrite credential files when a
    # token refreshes.
    for harness in _harnesses_for(workflow_abs):
        _ensure_state_volume(harness)
        cmd += _harness_args(harness, interactive=False)
    cmd += ["giver:latest", "/workflow.yaml"]
    subprocess.run(cmd)


def _interactive(harness_name: str | None, entrypoint: list[str]) -> int:
    """An interactive container, entered through give'r rather than directly.

    The command is reached via `giver.harness`, which prepares the harness in
    the container and then execs it. Mounting a harness's state is only half of
    provisioning it — a harness that keeps state outside the directory give'r
    mounts has to reconcile that first, and here is the only place give'r code
    runs on this path.
    """
    _ensure_image()
    cmd = ["docker", "run", "--rm", "-it", *_user_args()]
    prepare: list[str] = []
    if harness_name is not None:
        try:
            harness = harness_by_name(harness_name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _ensure_state_volume(harness)
        cmd += _harness_args(harness, interactive=True)
        prepare = ["--harness", harness.name]
    cmd += ["--entrypoint", "python", "giver:latest"]
    cmd += ["-m", "giver.harness", *prepare, "--", *entrypoint]
    return subprocess.run(cmd).returncode


def shell(harness: str | None = None) -> int:
    """Bash inside the sandbox with a harness's state volume mounted — the
    manual first-pass auth path. Bare `giver shell` is a plain container."""
    return _interactive(harness, ["bash"])


def chat(harness: str) -> int:
    """The harness's own REPL, same provisioning as `shell`."""
    try:
        repl_cmd = list(harness_by_name(harness).repl_cmd)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return _interactive(harness, repl_cmd)


def _stream(name: str) -> int:
    subprocess.run(["docker", "logs", "-f", name])
    result = subprocess.run(["docker", "wait", name], capture_output=True, text=True)
    return int(result.stdout.strip())


def run(workflow_path: Path, runs_dir: Path | None = None, detach: bool = False) -> int:
    if runs_dir is None:
        runs_dir = Path.cwd() / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    workflow_abs = workflow_path.resolve()
    name = _container_name(workflow_path.stem)
    _log(runs_dir, f"start container={name} workflow={workflow_abs} at {datetime.now(timezone.utc).isoformat()}")

    _ensure_image()
    _run_container(workflow_abs, runs_dir, name)

    if detach:
        print(name)
        return 0

    exit_code = _stream(name)
    _log(runs_dir, f"exit {exit_code} container={name} at {datetime.now(timezone.utc).isoformat()}")
    return exit_code


def cancel(name: str) -> int:
    return subprocess.run(["docker", "stop", name]).returncode


def _log(runs_dir: Path, message: str) -> None:
    with (runs_dir / "runs.log").open("a") as f:
        f.write(message + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(prog="giver")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run a workflow in Docker")
    run_p.add_argument("workflow", type=Path, help="path to workflow YAML")
    run_p.add_argument("--detach", "-d", action="store_true", help="start and exit; print container name")

    cancel_p = sub.add_parser("cancel", help="stop a running workflow container")
    cancel_p.add_argument("name", help="container name (from runs.log or giver run --detach)")

    shell_p = sub.add_parser("shell", help="open an interactive shell in the giver container")
    shell_p.add_argument(
        "harness",
        nargs="?",
        choices=HARNESS_NAMES,
        help="harness whose state to mount (omit for a bare container shell)",
    )

    chat_p = sub.add_parser("chat", help="open a harness's own REPL in the giver container")
    chat_p.add_argument("harness", choices=HARNESS_NAMES, help="harness to launch")

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(run(args.workflow, detach=args.detach))
    elif args.command == "cancel":
        sys.exit(cancel(args.name))
    elif args.command == "shell":
        sys.exit(shell(args.harness))
    elif args.command == "chat":
        sys.exit(chat(args.harness))
