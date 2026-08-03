import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from giver.harness import HARNESS_NAMES, Harness, harness_by_name
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


# A harness describes itself in its own terms — pi says `~/.pi/agent`. These
# translate that into the Docker names for a root container, which is knowledge
# the host has and the harness shouldn't.
def _volume(harness: Harness) -> str:
    return f"giver-{harness.name}-creds"


def _container_path(harness: Harness) -> str:
    return "/root/" + harness.state_path.removeprefix("~/")


def _harness_args(harness: Harness, publish_ports: bool) -> list[str]:
    """Docker arguments a harness needs: its environment, its credential and
    session volume, and — only when someone will interact with it — the ports
    its login flow listens on.

    A run publishes no ports: logging in happens beforehand via `giver shell`,
    and a headless step has nothing to answer an OAuth callback with.
    """
    args = []
    for key, value in harness.env.items():
        args += ["-e", f"{key}={value}"]
    if publish_ports:
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
    named = {node.harness_name() for node in workflow.nodes} - {None}
    return [harness_by_name(name) for name in sorted(named, key=lambda n: n or "")]


def _run_container(workflow_abs: Path, runs_dir: Path, name: str) -> None:
    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "-v", f"{workflow_abs}:/workflow.yaml:ro",
        "-v", f"{runs_dir}:/runs",
    ]
    # Mount writable: harnesses write session transcripts as they work and
    # rewrite credential files when a token refreshes.
    for harness in _harnesses_for(workflow_abs):
        cmd += _harness_args(harness, publish_ports=False)
    cmd += ["giver:latest", "/workflow.yaml"]
    subprocess.run(cmd)


def _interactive(harness_name: str | None, entrypoint: list[str]) -> int:
    _ensure_image()
    cmd = ["docker", "run", "--rm", "-it"]
    if harness_name is not None:
        try:
            harness = harness_by_name(harness_name)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        cmd += _harness_args(harness, publish_ports=True)
    cmd += ["--entrypoint", *entrypoint, "giver:latest"]
    return subprocess.run(cmd).returncode


def shell(harness: str | None = None) -> int:
    """Bash inside the sandbox with a harness's credential volume mounted —
    the manual first-pass auth path. Bare `giver shell` is a plain container."""
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
        help="harness whose credentials to mount (omit for a bare container shell)",
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
