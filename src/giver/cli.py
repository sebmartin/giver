import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _container_name(workflow_stem: str) -> str:
    return f"giver-{workflow_stem}-{int(time.time())}"


def _start(workflow_abs: Path, runs_dir: Path, name: str) -> None:
    subprocess.run([
        "docker", "run", "-d",
        "--name", name,
        "-v", f"{workflow_abs}:/workflow.yaml:ro",
        "-v", f"{runs_dir}:/runs",
        "-e", "ANTHROPIC_API_KEY",
        "giver:latest",
        "/workflow.yaml",
    ])


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

    _start(workflow_abs, runs_dir, name)

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

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(run(args.workflow, detach=args.detach))
    elif args.command == "cancel":
        sys.exit(cancel(args.name))
