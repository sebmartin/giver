"""Impersonate the uid in `GIVER_UID`, then exec the command.

Creates the account, takes ownership of its home and harness state, and drops
privileges. With `GIVER_UID` unset it execs as whoever it already is.
"""

import grp
import os
import pwd
import subprocess
import sys
from pathlib import Path

HOME = "/home/giver"
WORKDIR = "/work"
USER = "giver"


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("error: nothing to exec", file=sys.stderr)
        raise SystemExit(1)

    requested = os.environ.get("GIVER_UID")
    uid = int(requested or os.geteuid())
    _require_not_root(uid)

    if requested:
        # Impersonate the requested identity
        gid = int(os.environ.get("GIVER_GID") or uid)
        name = _ensure_account(uid, gid)
        _take_ownership(uid, gid)
        _become(name, uid, gid)
    else:
        _require_writable_home()

    os.execvp(argv[0], argv)


def _require_not_root(uid: int) -> None:
    """Exit when the process would run as uid 0.

    claude-code refuses to run headless as root (issue #9), and files it writes
    are then owned by someone the caller cannot delete.
    """
    if uid != 0:
        return
    print(
        "error: refusing to run as root, because harnesses that refuse to run "
        "privileged will fail. Set GIVER_UID to an unprivileged uid.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _require_writable_home() -> None:
    """Exit when `$HOME` is missing or not writable.

    Harnesses resolve `~` from `$HOME`. Runs after `_require_not_root`, since
    `os.access` reports almost everything as writable to root.
    """
    home = Path(os.environ.get("HOME", HOME))
    if home.is_dir() and os.access(home, os.W_OK):
        return
    print(
        f"error: $HOME ({home}) does not exist or is not writable. Set GIVER_UID to have the account and home created.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _ensure_account(uid: int, gid: int) -> str:
    """Create a passwd entry for this uid, and a home to go with it.

    The group comes first because `useradd -g` fails when that gid has no
    group, and Debian ships no gid 1000.
    """
    try:
        grp.getgrgid(gid)
    except KeyError:
        _quietly(["groupadd", "-g", str(gid), USER])

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        pass

    _quietly(["useradd", "-u", str(uid), "-g", str(gid), "-m", "-d", HOME, "-s", "/bin/bash", USER])
    return USER


def _quietly(cmd: list[str]) -> None:
    """Run `cmd`, printing only when it fails.

    `useradd` warns on every start about a macOS uid below Debian's UID_MIN and
    about a home directory a mount already created.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: {' '.join(cmd)} failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)


def _take_ownership(uid: int, gid: int) -> None:
    """Give this user its home and working directory, and all their contents.

    Docker creates a volume's mount point, and the directories above it, owned
    by root, so this runs on every start.
    """
    home, work = Path(HOME), Path(WORKDIR)
    home.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    to_chown = _unowned(work, uid)
    # $HOME does not stand for what is under it: a volume mounted into an
    # already-converted home still arrives owned by root.
    for child in home.iterdir():
        to_chown += _unowned(child, uid)
    if home.stat().st_uid != uid:
        to_chown.append(home)

    for path in to_chown:
        os.chown(path, uid, gid)
    # Docker leaves a mounted home 0755; `useradd -m` makes one 0700.
    home.chmod(0o700)


def _unowned(path: Path, uid: int) -> list[Path]:
    """Return what `path` covers that `uid` does not own, contents first.

    A directory this user already owns is skipped whole, since an earlier run
    converted everything below it. Contents come first so a directory is
    chowned after everything under it, and a run that dies partway is repeated
    rather than skipped.
    """
    if path.stat().st_uid == uid:
        return []
    below: list[Path] = []
    if path.is_dir():
        for child in path.iterdir():
            below += _unowned(child, uid)
    return below + [path]


def _become(name: str, uid: int, gid: int) -> None:
    """Drop privileges to `uid` and `gid`.

    Groups first: `setuid` gives away the privilege that `initgroups` and
    `setgid` require.
    """
    os.initgroups(name, gid)
    os.setgid(gid)
    os.setuid(uid)
    os.environ["USER"] = name
    os.environ["LOGNAME"] = name


if __name__ == "__main__":
    main()
