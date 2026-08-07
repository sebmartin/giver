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

Bypassing this is always safe: with no `GIVER_UID` it execs unchanged. That is
the case where somebody else started the container — a CI job container has its
own user, its own home and its own mounts, and has already done this job.
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
        os.execvp(argv[0], argv)
        return  # execvp replaces this process; returning is not control flow

    uid = int(requested)
    gid = int(os.environ.get("GIVER_GID") or uid)
    if uid == 0:
        # `sudo giver run`. Dropping to root is not dropping, and a harness
        # running unattended as root is what issue #9 was: claude-code refuses
        # the combination outright, so this would fail anyway — several minutes
        # later, reported as a harness error rather than as its cause.
        print(
            "error: give'r was run as root, and the container would run as root too. "
            "Harnesses that refuse to run privileged will fail. Run give'r as "
            "an unprivileged user.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    name = _ensure_account(uid, gid)
    _take_ownership(uid, gid)
    _become(name, uid, gid)
    os.execvp(argv[0], argv)


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
        # Mounting a volume at ~/.pi/agent makes Docker create ~/.pi on the way
        # to it, owned by root — a directory in this user's own home that it
        # does not own, waiting for the first harness to write beside its state
        # rather than inside it.
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
