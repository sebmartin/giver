from pathlib import Path
from types import SimpleNamespace

import pytest

from giver.image import render
from giver.harness import NODE, ClaudeCodeHarness, PiHarness


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
