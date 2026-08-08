import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import pytest

from giver import entrypoint
from giver.entrypoint import main

MINE = os.getuid()
SOMEONE_ELSE = 4321  # whoever wrote the volume before this container existed


@pytest.fixture
def container(tmp_path, monkeypatch):
    """The container's filesystem: a home, a working directory, and whatever
    mounts a test says Docker made under the home.

    Real directories rather than a patched `Path`, so what the entrypoint sees
    of existence and ownership is what a filesystem actually reports.
    """
    home, work = tmp_path / "home" / "giver", tmp_path / "work"
    monkeypatch.setattr(entrypoint, "HOME", str(home))
    monkeypatch.setattr(entrypoint, "WORKDIR", str(work))
    monkeypatch.setenv("HOME", str(home))

    def mount(relative: str) -> Path:
        """What Docker does for `-v vol:$HOME/<relative>` — create that path,
        and every directory above it, before the container starts."""
        path = home / relative
        path.mkdir(parents=True)
        return path

    return SimpleNamespace(home=home, work=work, mount=mount)


def _drive(argv, env, existing_uids=(0,), existing_gids=(0,), running_as=1000):
    """Run `main`, with the account database and privilege drop stubbed.

    `existing_uids`/`existing_gids` are what /etc/passwd and /etc/group already
    hold. Ownership is not stubbed: the directories are really owned by whoever
    runs the tests, so asking for that uid means nothing needs chowning and
    asking for another means everything does.

    `running_as` is the uid the container already has, which only matters on the
    path where nobody sent one. Stubbed so a suite run inside a container — as
    root, the obvious way to exercise this file for real — does not take a
    different branch than the same suite run on a laptop.
    """
    with (
        patch.dict(os.environ, env),
        patch("giver.entrypoint.os.geteuid", return_value=running_as),
        patch("giver.entrypoint.os.execvp") as execvp,
        patch("giver.entrypoint.subprocess.run") as sub,
        patch("giver.entrypoint.os.initgroups") as initgroups,
        patch("giver.entrypoint.os.setgid") as setgid,
        patch("giver.entrypoint.os.setuid") as setuid,
        patch("giver.entrypoint.os.chown") as chown,
        patch("giver.entrypoint.pwd.getpwuid") as getpwuid,
        patch("giver.entrypoint.grp.getgrgid") as getgrgid,
    ):
        sub.return_value = SimpleNamespace(returncode=0, stderr="")
        getpwuid.side_effect = lambda u: (
            SimpleNamespace(pw_name=f"u{u}") if u in existing_uids else _missing()
        )
        getgrgid.side_effect = lambda g: g if g in existing_gids else _missing()
        main(argv)

    return SimpleNamespace(
        execvp=execvp.call_args,
        shell=[c[0][0] for c in sub.call_args_list],
        chowned={Path(c[0][0]) for c in chown.call_args_list},
        became=(initgroups.call_args, setgid.call_args, setuid.call_args),
    )


def _missing():
    raise KeyError


# ── when give'r did not start this container ──────────────────────────────────


def test_execs_untouched_without_a_uid(container):
    """Someone else started it — a CI job container has its own user, home and
    mounts, and has already done all of this. Skipping the entrypoint has to be
    a no-op, or every such runtime becomes a special case."""
    container.home.mkdir(parents=True)

    result = _drive(["python", "-m", "giver.kernel"], env={})

    assert result.execvp == call("python", ["python", "-m", "giver.kernel"])
    assert (result.shell, result.became) == ([], (None, None, None))


def test_refuses_to_exec_into_a_container_with_no_home(container):
    """`docker run --user 1000` on a generated image is not root, so it clears
    that check and still has `$HOME` pointing at a directory `useradd -m` never
    created. Every harness reads `~` from `$HOME`."""
    with pytest.raises(SystemExit) as exc:
        _drive(["python", "-m", "giver.kernel"], env={}, running_as=1000)

    assert exc.value.code == 1


def test_refuses_to_exec_into_a_container_already_running_as_root(container):
    """A writable home is no evidence at uid 0, where almost everything is
    writable — mounting any volume over `$HOME` would satisfy it. So running as
    root is refused before the home is looked at, the same as a uid give'r
    sent."""
    container.home.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        _drive(["python", "-m", "giver.kernel"], env={}, running_as=0)

    assert exc.value.code == 1


def test_refuses_to_drop_to_root(container):
    """`sudo giver run`. Dropping to root is not dropping, and claude-code
    refuses to run privileged — so this fails either way, and failing here
    names the cause instead of surfacing later as a harness error."""
    with pytest.raises(SystemExit) as exc:
        _drive(["bash"], env={"GIVER_UID": "0"})

    assert exc.value.code == 1


# ── making an account ─────────────────────────────────────────────────────────


def test_creates_the_group_before_the_user(container):
    """`useradd -g` fails outright when that gid has no group, and Debian has
    no gid 1000 — the primary gid of the first user on most Linux hosts. macOS
    hides it, because gid 20 happens to exist there as `dialout`."""
    result = _drive(["bash"], env={"GIVER_UID": "1000", "GIVER_GID": "1000"})

    assert result.shell[0] == ["groupadd", "-g", "1000", "giver"]
    assert result.shell[1][:6] == ["useradd", "-o", "-u", "1000", "-g", "1000"]


def test_reuses_a_group_that_already_exists(container):
    """A macOS gid lands on one of Debian's own groups — 20 is `dialout` — and
    groupadd errors rather than doing nothing."""
    result = _drive(["bash"], env={"GIVER_UID": "501", "GIVER_GID": "20"}, existing_gids=(0, 20))

    assert not any(c[0] == "groupadd" for c in result.shell)


def test_reuses_an_account_that_already_exists_and_becomes_it(container):
    """A uid the base image ships, or a container restarted with its writable
    layer intact, already has an entry that `useradd` would fail on."""
    result = _drive(
        ["bash"],
        env={"GIVER_UID": "33", "GIVER_GID": "33"},
        existing_uids=(0, 33),
        existing_gids=(0, 33),
    )

    assert not any(c[0] == "useradd" for c in result.shell)
    assert result.became[0] == call("u33", 33)


def test_gid_defaults_to_the_uid(container):
    result = _drive(["bash"], env={"GIVER_UID": "1000"})

    assert result.became[1] == call(1000)


# ── ownership ─────────────────────────────────────────────────────────────────


def test_gives_the_user_a_home_and_a_working_directory(container):
    result = _drive(["bash"], env={"GIVER_UID": str(SOMEONE_ELSE)})

    assert {container.home, container.work} <= result.chowned


def test_home_is_private_however_it_came_to_exist(container):
    """A home a mount created is 0755; one `useradd -m` created is 0700. A home
    that differs by whether something happened to be mounted is the kind of
    surprise this entrypoint exists to remove."""
    container.mount(".pi/agent")  # creates the home as a side effect, 0755

    _drive(["bash"], env={"GIVER_UID": str(MINE)})

    assert stat.S_IMODE(container.home.stat().st_mode) == 0o700


def test_takes_ownership_of_state_written_by_an_earlier_root_container(container):
    """Docker creates a volume root-owned when the image carries nothing at
    that path, and everything an older give'r wrote into one belongs to root.
    A top-level chown would leave a writable directory full of unreadable
    credentials — which reads as logged out with a valid token inside it."""
    container.mount(".pi/agent")

    result = _drive(["bash"], env={"GIVER_UID": str(SOMEONE_ELSE), "GIVER_GID": "20"})

    assert ["chown", "-R", f"{SOMEONE_ELSE}:20", str(container.home / ".pi/agent")] in result.shell


def test_claims_the_directories_a_mount_created_on_its_way_down(container):
    """Mounting at ~/.pi/agent makes Docker create ~/.pi too, owned by root — a
    directory in this user's own home that it does not own, waiting for the
    first harness to write beside its state rather than inside it."""
    container.mount(".pi/agent")

    result = _drive(["bash"], env={"GIVER_UID": str(SOMEONE_ELSE)})

    assert container.home / ".pi" in result.chowned


def test_leaves_state_alone_once_it_is_already_owned(container):
    """Steady state is one stat per mount, not a recursive walk of every
    session transcript on every run."""
    container.mount(".pi/agent")

    result = _drive(["bash"], env={"GIVER_UID": str(MINE)})

    assert not any(c[0] == "chown" for c in result.shell)
    assert result.chowned == set()


def test_ignores_state_for_a_harness_this_image_does_not_carry(container):
    """An image built for a pi workflow has no claude directory and needs none
    — creating one would put a harness's private knowledge in every image."""
    container.mount(".pi/agent")

    _drive(["bash"], env={"GIVER_UID": str(MINE)})

    assert not (container.home / ".claude").exists()


# ── becoming the program ──────────────────────────────────────────────────────


def test_drops_privileges_then_execs_what_it_was_given(container):
    result = _drive(["bash", "-l"], env={"GIVER_UID": "501", "GIVER_GID": "20"})

    assert result.became == (call("giver", 20), call(20), call(501))
    assert result.execvp == call("bash", ["bash", "-l"])
