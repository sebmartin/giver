from pathlib import Path
from types import SimpleNamespace

import pytest

from giver.harness import NODE, ClaudeCodeHarness, PiHarness
from giver.image import render, source_fingerprint, tag


def _harness(name, install, toolchain=None):
    """A stand-in for a harness: the generator reads three fields and calls
    nothing, so anything carrying them is a harness as far as it is concerned."""
    return SimpleNamespace(name=name, install=install, toolchain=toolchain)


def _runs(text: str) -> list[str]:
    """The RUN commands, in order — what the image actually does."""
    return [
        line.removeprefix("RUN ") for line in text.splitlines() if line.startswith("RUN ")
    ]


@pytest.fixture
def checkout(tmp_path) -> Path:
    """A directory that looks enough like a give'r checkout to build from."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "giver"\n')
    return tmp_path


# ── contents ──────────────────────────────────────────────────────────────────


def test_installs_each_harness_and_its_toolchain_once(checkout):
    """Two harnesses needing node get one node layer, not two — the whole
    reason `toolchain` is a shared constant rather than a copied string."""
    text = render([PiHarness(), ClaudeCodeHarness()], dev=checkout)

    assert _runs(text) == [
        NODE,
        "npm install -g @anthropic-ai/claude-code",
        "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
        "pip install .",
    ]


def test_omits_the_toolchain_when_nothing_declares_one(checkout):
    """node exists for the harnesses, not for give'r — an image carrying only
    harnesses that do not need it should not carry it."""
    text = render([_harness("nodeless", "pip install some-agent")], dev=checkout)

    assert _runs(text) == ["pip install some-agent", "pip install ."]


def test_emits_distinct_toolchains_separately(checkout):
    """Nothing resolves a toolchain, so a harness needing something other than
    node needs no change here — it just declares its own command."""
    text = render(
        [
            _harness("a", "install-a", toolchain="setup-rust"),
            _harness("b", "install-b", toolchain=NODE),
        ],
        dev=checkout,
    )

    assert _runs(text)[:2] == ["setup-rust", NODE]


def test_orders_harnesses_by_name_whatever_order_they_arrive_in(checkout):
    """Same set, same bytes — so a regenerate-and-diff check is meaningful and
    Docker's layer cache is not invalidated by an incidental reordering."""
    forwards = render([PiHarness(), ClaudeCodeHarness()], dev=checkout)
    backwards = render([ClaudeCodeHarness(), PiHarness()], dev=checkout)

    assert forwards == backwards


# ── the label ─────────────────────────────────────────────────────────────────


def test_labels_the_image_with_what_it_carries(checkout):
    """The label, not the tag, says what a runtime contains — whoever builds
    owns the tag, so its shape cannot be relied on."""
    text = render([PiHarness(), ClaudeCodeHarness()], dev=checkout)

    assert 'LABEL giver.harnesses="claude-code,pi"' in text


def test_labels_an_empty_harness_set(checkout):
    """A workflow of nothing but bash nodes still needs an image."""
    text = render([], dev=checkout)

    assert 'LABEL giver.harnesses=""' in text
    assert _runs(text) == ["pip install ."]


# ── the environment the image hands over ──────────────────────────────────────


def test_entrypoint_is_the_part_nothing_should_skip(checkout):
    """ENTRYPOINT holds what is invariant — making the container sane for
    whatever uid it is told. The program is the command, so wanting a different
    one replaces CMD rather than overriding the entrypoint and skipping the
    privilege drop with it."""
    text = render([PiHarness()], dev=checkout)

    assert 'ENTRYPOINT ["python", "-m", "giver.entrypoint"]' in text
    assert 'CMD ["python", "-m", "giver.kernel"]' in text


def test_work_happens_somewhere_other_than_giver_s_own_source(checkout):
    """/app is root-owned, and does not exist at all once give'r installs from
    PyPI — so cwd must not be it, or it differs between the two."""
    text = render([PiHarness()], dev=checkout)
    lines = text.splitlines()

    assert "ENV HOME=/home/giver" in lines
    assert lines.index("WORKDIR /work") > lines.index("WORKDIR /app")


# ── identity ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "harnesses,expected",
    [
        ([], "giver:dev-base"),
        ([PiHarness()], "giver:dev-pi"),
        ([ClaudeCodeHarness(), PiHarness()], "giver:dev-claude-code_pi"),
        ([PiHarness(), ClaudeCodeHarness()], "giver:dev-claude-code_pi"),
    ],
)
def test_tag_names_the_harness_set(harnesses, expected):
    """Distinct sets are distinct images. One image grown to cover everything a
    machine has run would be a combination nobody chose and nobody tested, and
    would differ between two people running the same workflow."""
    assert tag(harnesses, dev=Path(".")) == expected


def test_tag_separates_a_checkout_from_a_release(checkout):
    """Same harnesses, different give'r — sharing a tag would silently reuse
    one for the other."""
    assert tag([PiHarness()], dev=checkout) != tag([PiHarness()])


def test_a_dev_image_records_the_source_it_was_built_from(checkout):
    """A version does not move while someone edits, so it cannot answer whether
    the give'r in an image is the give'r being run."""
    text = render([PiHarness()], dev=checkout)

    assert f'LABEL giver.source="{source_fingerprint(checkout)}"' in text


def test_the_fingerprint_follows_the_source(checkout):
    before = source_fingerprint(checkout)
    (checkout / "src").mkdir()
    (checkout / "src" / "thing.py").write_text("x = 1")

    assert source_fingerprint(checkout) != before


def test_the_fingerprint_ignores_what_the_image_never_sees(checkout):
    """Bytecode is excluded from the build context, so hashing it would rebuild
    the image every time something imported give'r."""
    (checkout / "src" / "__pycache__").mkdir(parents=True)
    before = source_fingerprint(checkout)
    (checkout / "src" / "__pycache__" / "m.cpython-313.pyc").write_bytes(b"\x00")

    assert source_fingerprint(checkout) == before


# ── where give'r comes from ───────────────────────────────────────────────────


def test_dev_build_copies_the_checkout(checkout):
    text = render([], dev=checkout)

    assert "COPY pyproject.toml uv.lock ./" in text
    assert "COPY src/ ./src/" in text


def test_refuses_to_install_giver_from_pypi():
    """`giver` is not a registered PyPI name. Emitting `pip install giver`
    would hand whoever claims it the container holding every credential."""
    with pytest.raises(ValueError, match="not published to PyPI"):
        render([PiHarness()], dev=None)


def test_rejects_a_dev_path_that_is_not_a_checkout(tmp_path):
    """Fail while the path is still in someone's hand, rather than as a COPY
    error inside a build."""
    with pytest.raises(ValueError, match="no pyproject.toml"):
        render([PiHarness()], dev=tmp_path)
