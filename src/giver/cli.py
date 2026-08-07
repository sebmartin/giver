import argparse
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from giver.entrypoint import HOME
from giver.harness import HARNESS_NAMES, HARNESSES, Harness, harness_by_name
from giver.image import SOURCE_LABEL, render, source_fingerprint, tag
from giver.kernel.nodes.agent import AgentNode
from giver.kernel.workflow import Workflow


def _container_name(workflow_stem: str) -> str:
    return f"giver-{workflow_stem}-{int(time.time())}"


_PROJECT_ROOT = Path(__file__).parents[2]


def _label(image: str, key: str) -> str | None:
    """One label off a local image, or None if there is no such image.

    Answerable before any container starts, which is what keeps this a
    preflight rather than something a run discovers halfway through.
    """
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", f'{{{{index .Config.Labels "{key}"}}}}'],
        capture_output=True,
        text=True,
    )
    return None if result.returncode != 0 else result.stdout.strip()


def _ensure_image(harnesses: Iterable[Harness] = ()) -> str:
    """A local image carrying exactly these harnesses. Returns its tag.

    Each distinct set gets its own tag. Growing a single image to cover
    everything this machine has run would make its contents depend on that
    history, so two people running the same workflow would get different
    images, each holding a combination of harnesses nothing was tested against.
    The duplication is cheap: the base image and the toolchain are shared
    layers, and node is installed before any harness.

    A rebuild is also needed when the give'r inside an existing tag has changed.
    The version does not change while someone edits the source, so a fingerprint
    of the source decides.
    """
    try:
        dockerfile_text = render(harnesses, dev=_PROJECT_ROOT)
        fingerprint = source_fingerprint(_PROJECT_ROOT)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    image = tag(harnesses, dev=_PROJECT_ROOT)
    if _label(image, SOURCE_LABEL) == fingerprint:
        return image

    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        print("error: Docker is not running", file=sys.stderr)
        sys.exit(1)
    # `-f -` reads the Dockerfile from stdin while still taking a build context
    # from the path — `docker build -` would send the context itself and leave
    # the COPY lines nothing to copy.
    result = subprocess.run(
        ["docker", "build", "-t", image, "-f", "-", str(_PROJECT_ROOT)],
        input=dockerfile_text,
        text=True,
    )
    if result.returncode != 0:
        print("error: image build failed", file=sys.stderr)
        sys.exit(1)
    return image


def _user_args() -> list[str]:
    """Who the container should run as.

    A run writes its logs and artifacts onto a bind mount, and on a Linux host
    the uid crosses that boundary unchanged, so files the container creates have
    to already belong to whoever will read them. It also must not be root:
    claude-code refuses to run headless as uid 0.

    Passed at run time rather than baked into the image, because the image is
    portable and this machine's uid is not. Omitted on Windows, which has no
    `geteuid`; the container's own user is the right answer there.
    """
    if not hasattr(os, "geteuid"):
        return []
    return ["-e", f"GIVER_UID={os.getuid()}", "-e", f"GIVER_GID={os.getgid()}"]


# A harness describes itself in its own terms — pi says `~/.pi/agent`. These
# translate that into the Docker names for the container, which is knowledge
# the host has and the harness shouldn't.
def _volume(harness: Harness) -> str:
    # `state`, not `creds`: it is the harness's whole `state_path` — sessions,
    # config and project history as much as the token.
    return f"giver-{harness.name}-state"


def _container_path(harness: Harness) -> str:
    return f"{HOME}/" + harness.state_path.removeprefix("~/")


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


def _harnesses_for(*workflow_paths: Path) -> list[Harness]:
    """The harnesses these workflows' agent nodes actually name.

    Parsing the workflow host-side means an unknown harness or an unresolvable
    model is reported here, before a container exists, using the same code the
    kernel will run inside it.

    Takes several because an image is built for a set of workflows, even though
    a run only ever concerns one.
    """
    named: set[str] = set()
    for path in workflow_paths:
        workflow = Workflow.from_file(path)
        named |= {n.harness_name for n in workflow.nodes if isinstance(n, AgentNode)}
    return [harness_by_name(name) for name in sorted(named)]


def _run_container(
    workflow_abs: Path, runs_dir: Path, name: str, harnesses: list[Harness], image: str
) -> None:
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
    for harness in harnesses:
        cmd += _harness_args(harness, interactive=False)
    cmd += [image, "python", "-m", "giver.kernel", "/workflow.yaml"]
    subprocess.run(cmd)


def _interactive(harness_name: str | None, entrypoint: list[str]) -> int:
    """An interactive container, entered through give'r rather than directly.

    The command is reached via `giver.harness`, which prepares the harness in
    the container and then execs it. Mounting a harness's state is only half of
    provisioning it — a harness that keeps state outside the directory give'r
    mounts has to reconcile that first, and here is the only place give'r code
    runs on this path.
    """
    harnesses: list[Harness] = []
    if harness_name is not None:
        try:
            harnesses = [harness_by_name(harness_name)]
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    image = _ensure_image(harnesses)
    cmd = ["docker", "run", "--rm", "-it", *_user_args()]
    prepare: list[str] = []
    for harness in harnesses:
        cmd += _harness_args(harness, interactive=True)
        prepare = ["--harness", harness.name]
    # Passed as a command rather than an entrypoint override. The image's
    # entrypoint sets the container up for this uid, and these paths write the
    # first-pass login, so they must not skip it.
    cmd += [image, "python", "-m", "giver.harness", *prepare, "--", *entrypoint]
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

    harnesses = _harnesses_for(workflow_abs)
    image = _ensure_image(harnesses)
    _run_container(workflow_abs, runs_dir, name, harnesses, image)

    if detach:
        print(name)
        return 0

    exit_code = _stream(name)
    _log(runs_dir, f"exit {exit_code} container={name} at {datetime.now(timezone.utc).isoformat()}")
    return exit_code


def cancel(name: str) -> int:
    return subprocess.run(["docker", "stop", name]).returncode


def dockerfile(workflows: list[Path] | None = None, dev: Path | None = None) -> int:
    """Print the Dockerfile for a runtime carrying these workflows' harnesses.

    give'r does not build images for anyone but itself — this is what a CI
    pipeline, or anyone who wants their own runtime, builds from. With no
    workflow named it emits every registered harness, which is the image that
    can run anything.
    """
    # A workflow of nothing but bash nodes names no harness and should get an
    # image carrying none — which is not the same as naming no workflow at all.
    if workflows:
        harnesses = _harnesses_for(*(w.resolve() for w in workflows))
    else:
        harnesses = list(HARNESSES)
    try:
        print(render(harnesses, dev=dev), end="")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


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

    dockerfile_p = sub.add_parser("dockerfile", help="print the Dockerfile for a runtime")
    dockerfile_p.add_argument(
        "workflow",
        nargs="*",
        type=Path,
        help="workflows the image is for (omit for every registered harness)",
    )
    dockerfile_p.add_argument(
        "--dev",
        nargs="?",
        const=Path("."),
        default=None,
        type=Path,
        metavar="PATH",
        help="build give'r from this checkout rather than from PyPI (default: .)",
    )

    args = parser.parse_args()
    if args.command == "run":
        sys.exit(run(args.workflow, detach=args.detach))
    elif args.command == "cancel":
        sys.exit(cancel(args.name))
    elif args.command == "shell":
        sys.exit(shell(args.harness))
    elif args.command == "chat":
        sys.exit(chat(args.harness))
    elif args.command == "dockerfile":
        sys.exit(dockerfile(args.workflow, dev=args.dev))
