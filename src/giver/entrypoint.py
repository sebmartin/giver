"""Make the container an ordinary environment for the user who ran give'r,
then become the program that was asked for.

Nothing above this should be able to tell it is in a sandbox. A workflow runs
arbitrary programs, and those programs expect what any Unix machine provides: an
account, a writable home directory, and a working directory they own. Anything
missing shows up later as a failure in whatever workflow first depends on it.

The uid cannot be baked into the image. give'r is a tool other people build and
publish images for, and a uid only ever matches the machine that chose it —
Docker has no facility for mapping one to another (`--userns` takes only
`host`; the per-container mapping podman calls `keep-id` has no equivalent).
So the image carries no user at all and this creates one, for whatever uid it
is told, before dropping to it.

With no `GIVER_UID` it execs unchanged, for the case where somebody else started
the container: a CI job container has its own user, home and mounts, and has
already done this job. It checks for both first, because the image give'r
generates has neither, and running it directly would otherwise land as root in
a home that does not exist.
"""

import grp
import os
import pwd
import subprocess
import sys
from pathlib import Path

from giver.harness import HARNESSES

HOME = "/home/giver"
WORKDIR = "/work"
USER = "giver"


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("error: nothing to exec", file=sys.stderr)
        raise SystemExit(1)

    requested = os.environ.get("GIVER_UID")
    if requested is None:
        # No uid was sent, so we are whatever the image started as and there is
        # no account to make: this can only check what it was given.
        _ensure_not_root(os.geteuid())
        _ensure_writable_home()
        os.execvp(argv[0], argv)
        return  # execvp replaces this process; returning is not control flow

    uid = int(requested)
    gid = int(os.environ.get("GIVER_GID") or uid)
    _ensure_not_root(uid)

    name = _ensure_account(uid, gid)
    _take_ownership(uid, gid)
    _become(name, uid, gid)
    os.execvp(argv[0], argv)


def _ensure_not_root(uid: int) -> None:
    """Refuse a container that would run as uid 0.

    claude-code refuses to run headless as root (issue #9), so a workflow that
    reaches a harness fails several minutes in and reports it as a harness
    error. On Linux the uid also crosses the /runs bind mount unchanged, so the
    logs and artifacts come out owned by a user who cannot delete them.

    Called with the uid give'r sent, and on the other path with the uid the
    container already has.
    """
    if uid != 0:
        return
    print(
        "error: this container would run as root. Harnesses that refuse to run "
        "privileged will fail, and anything written to /runs comes out "
        "root-owned. Run give'r as an unprivileged user, or start the container "
        "with -e GIVER_UID=$(id -u) -e GIVER_GID=$(id -g).",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _ensure_writable_home() -> None:
    """Refuse to exec when `$HOME` is missing or not writable.

    An unset `GIVER_UID` means somebody else started this container, and theirs
    will have a home. The image give'r generates has no user and no home, so
    `docker run --user 1000` on it passes the root check and still has `$HOME`
    pointing at a directory `useradd -m` never created. Harnesses resolve `~`
    from `$HOME` and would write credentials into a path that is not there.

    Runs after `_ensure_not_root` because `os.access` reports almost everything
    as writable to root, so at uid 0 it proves nothing.
    """
    home = Path(os.environ.get("HOME", HOME))
    if home.is_dir() and os.access(home, os.W_OK):
        return
    print(
        f"error: $HOME ({home}) does not exist or is not writable. An image "
        "give'r generated carries no user; run it with -e GIVER_UID=$(id -u) "
        "-e GIVER_GID=$(id -g) and this entrypoint will make the account.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _ensure_account(uid: int, gid: int) -> str:
    """An account for this uid, so `~` resolves the ordinary way.

    The group has to come first and separately: `useradd -g` fails outright if
    that gid has no group, and Debian has no gid 1000 — the primary gid of the
    first user on most Linux hosts. macOS hides this, because gid 20 happens to
    exist there as `dialout`.
    """
    try:
        grp.getgrgid(gid)
    except KeyError:
        _quietly(["groupadd", "-g", str(gid), USER])

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        pass
    # -o because a uid colliding with one the base image ships is the caller's
    # uid, not a mistake we get to refuse.
    _quietly(
        ["useradd", "-o", "-u", str(uid), "-g", str(gid),
         "-m", "-d", HOME, "-s", "/bin/bash", USER]
    )
    return USER


def _quietly(cmd: list[str]) -> None:
    """Run it, and say nothing unless it fails.

    These are chatty about things that are not problems here — a macOS uid is
    below Debian's UID_MIN, and the home directory always already exists
    because a mount created it. Every container start would otherwise open with
    a warning about neither.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: {' '.join(cmd)} failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)


def _take_ownership(uid: int, gid: int) -> None:
    """Hand this user its home, its working directory and its harness state.

    `useradd -m` creates a home only when there isn't one, and by the time this
    runs there always is: mounting a volume at `~/.pi/agent` makes Docker create
    every directory above it first, owned by root. Docker also creates the
    volume itself root-owned when the image carries nothing at that path, which
    is the whole of the provisioning problem — a non-root container cannot write
    the directory holding the credentials it was started to use.

    Recursive only when the top-level owner is wrong, so first use pays for a
    volume written by an older give'r and every run after it pays one stat.
    """
    home = Path(HOME)
    home.mkdir(parents=True, exist_ok=True)
    _own(home, uid, gid)
    # A home a mount created is 0755; one `useradd -m` created is 0700. Settle
    # it, so a home does not differ by whether anything happened to be mounted.
    home.chmod(0o700)

    work = Path(WORKDIR)
    work.mkdir(parents=True, exist_ok=True)
    _own(work, uid, gid)

    for harness in HARNESSES:
        state = Path(harness.state_path).expanduser()
        # Present because it was mounted. A harness this image doesn't carry
        # has no directory and needs none.
        if not state.exists():
            continue
        # Docker creates ~/.pi on its way to mounting ~/.pi/agent and leaves it
        # owned by root at 0755. No shipped harness writes anything there, so
        # this is not fixing a known failure: it is keeping the home directory
        # ordinary, since an account that cannot write inside its own home is
        # the kind of thing the container is supposed to hide.
        for parent in _under_home(state):
            _own(parent, uid, gid)
        if state.stat().st_uid != uid:
            _quietly(["chown", "-R", f"{uid}:{gid}", str(state)])


def _own(path: Path, uid: int, gid: int) -> None:
    if path.stat().st_uid != uid:
        os.chown(path, uid, gid)


def _under_home(state: Path) -> list[Path]:
    """The directories between the home directory and `state`, exclusive."""
    home, parents, path = Path(HOME), [], state.parent
    while path != home and path != path.parent:
        parents.append(path)
        path = path.parent
    return parents


def _become(name: str, uid: int, gid: int) -> None:
    """Drop, in the order the kernel allows: supplementary groups and the
    primary group while still privileged, the uid last."""
    os.initgroups(name, gid)
    os.setgid(gid)
    os.setuid(uid)
    os.environ["USER"] = name
    os.environ["LOGNAME"] = name


if __name__ == "__main__":
    main()
