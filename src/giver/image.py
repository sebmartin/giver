"""The Dockerfile for a runtime carrying a given set of harnesses.

An image is a function of the workflows it is built for: a harness is installed
because a workflow asked for it, which is what lets routing decide where a step
runs without first checking what happens to be present. A hand-written
Dockerfile could not do that, because it had to list the harnesses, so adding
one meant editing it.

give'r generates the file and does not build it. `giver dockerfile` prints it
for whoever wants an image; the CLI pipes the same text to `docker build` for
local runs. Both go through `render`, so what CI builds and what you run are
the same bytes.

`LABEL giver.harnesses` — not the tag — records what came out, because whoever
builds owns the tag and its shape cannot be relied on. It is readable with
`docker image inspect` before a container starts.
"""

import hashlib
from collections.abc import Iterable
from importlib.metadata import version
from pathlib import Path

from giver.entrypoint import HOME, WORKDIR
from giver.harness import Harness

BASE_IMAGE = "python:3.13-slim"

NAME = "giver"

# What an image carries, recorded where it can be read back without running it.
LABEL_KEY = "giver.harnesses"

# Which give'r is inside it. A version only moves on a release, so it says
# nothing while someone is editing the source a --dev image was built from.
SOURCE_LABEL = "giver.source"

# What ends up in the image, and therefore what changing means a new one is
# needed. Mirrors .dockerignore: bytecode is not in the build context, and
# hashing it would rebuild on every import.
_SOURCE_FILES = ("pyproject.toml", "uv.lock")
_IGNORED = ("__pycache__", ".egg-info", ".pyc")


def tag(harnesses: Iterable[Harness], dev: Path | None = None) -> str:
    """What to call an image carrying exactly `harnesses`.

    Contents, not history. An image built for a pi workflow and an image built
    for a claude workflow are different images, so running both on one machine
    leaves two — rather than one that has accumulated whatever this machine
    happens to have run, which is a combination nobody chose and nobody tested.

    Provenance is part of the identity too: an image built from a checkout
    carries that working tree, and one built from PyPI carries a release. Same
    harnesses, different give'r.
    """
    names = "_".join(sorted(h.name for h in harnesses)) or "base"
    return f"{NAME}:{'dev' if dev else version(NAME)}-{names}"


def source_fingerprint(dev: Path) -> str:
    """A digest of the give'r that would go into the image.

    The tag cannot carry this — a hash in a tag mints an image per edit, and
    every one of them is yours to prune. As a label it is just something to
    compare, and a rebuild replaces the tag it already had.
    """
    digest = hashlib.sha256()
    paths = [dev / name for name in _SOURCE_FILES]
    paths += sorted(p for p in (dev / "src").rglob("*") if p.is_file())
    for path in paths:
        if any(ignore in str(path) for ignore in _IGNORED) or not path.exists():
            continue
        digest.update(str(path.relative_to(dev)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def render(harnesses: Iterable[Harness], dev: Path | None = None) -> str:
    """The Dockerfile for a runtime carrying exactly `harnesses`.

    `dev` is a give'r checkout to build from. It is not written into the file —
    it is the build context — but whether there is one decides how give'r
    installs itself.

    Sorted by harness name so the same set always renders the same bytes:
    regenerating and diffing catches drift, and Docker's layer cache is not
    thrown away by an incidental reordering.
    """
    harnesses = sorted(harnesses, key=lambda h: h.name)

    sections = [
        [f"FROM {BASE_IMAGE}"],
        [f"RUN {command}" for command in _toolchains(harnesses)]
        + [f"RUN {harness.install}" for harness in harnesses],
        _install_giver(dev),
        [
            # Where the account the entrypoint creates will live, and where a
            # workflow's own work happens. /app holds give'r's build inputs and
            # is root-owned; it also does not exist at all once give'r installs
            # from PyPI, so cwd must not be it.
            f"ENV HOME={HOME}",
            f"WORKDIR {WORKDIR}",
        ],
        _labels(harnesses, dev)
        + [
            # ENTRYPOINT is what is invariant about this container: it makes the
            # environment sane for whatever uid it is told, then execs. The
            # program is the command, so wanting a different one — `giver shell`,
            # `giver chat`, a CI step — replaces CMD rather than overriding the
            # entrypoint and skipping the part nothing should skip.
            'ENTRYPOINT ["python", "-m", "giver.entrypoint"]',
            'CMD ["python", "-m", "giver.kernel"]',
        ],
    ]
    return "\n\n".join("\n".join(s) for s in sections if s) + "\n"


def _labels(harnesses: list[Harness], dev: Path | None) -> list[str]:
    """What this image is, readable without running it.

    A published image needs no fingerprint: its version already names exactly
    one give'r, and nobody can edit it afterwards.
    """
    labels = [f'LABEL {LABEL_KEY}="{",".join(h.name for h in harnesses)}"']
    if dev:
        labels.append(f'LABEL {SOURCE_LABEL}="{source_fingerprint(dev)}"')
    return labels


def _toolchains(harnesses: list[Harness]) -> list[str]:
    """Each distinct prerequisite, once, in the order first asked for.

    Deduplicated by string equality: harnesses that share a prerequisite share
    a constant, so an image carrying three npm harnesses installs node once.
    Nothing here knows what any of these commands do.
    """
    seen: list[str] = []
    for harness in harnesses:
        if harness.toolchain and harness.toolchain not in seen:
            seen.append(harness.toolchain)
    return seen


def _install_giver(dev: Path | None) -> list[str]:
    """Install give'r itself, from a checkout.

    The published form — `pip install giver==<version>`, no build context, so
    the file stands alone — is where this is going. It cannot be the default
    while `giver` is an unregistered name on PyPI: a generated file that
    installs it would, the moment someone claims the name, run their code in
    the one container that holds every harness credential. Delete this branch
    and emit the version once the name is ours.
    """
    if dev is None:
        raise ValueError(
            "give'r is not published to PyPI, so an image can only be built "
            "from a checkout — pass --dev [path]"
        )
    if not (dev / "pyproject.toml").exists():
        raise ValueError(f"no pyproject.toml under {dev} — not a give'r checkout")
    return [
        "WORKDIR /app",
        "COPY pyproject.toml uv.lock ./",
        "COPY src/ ./src/",
        "RUN pip install .",
    ]
