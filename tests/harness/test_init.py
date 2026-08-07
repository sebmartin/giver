import pytest

from giver.harness import (
    DEFAULT_HARNESS,
    HARNESSES,
    NODE,
    ClaudeCodeHarness,
    CodexHarness,
    PiHarness,
    harness_by_name,
    resolve_model,
    vendor_of,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("claude-opus-4-5", "anthropic/claude-opus-4-5"),
        ("gpt-5.5", "openai/gpt-5.5"),
        ("o3-mini", "openai/o3-mini"),
        ("gemini-3-pro", "google/gemini-3-pro"),
        ("anthropic/claude-opus-4-5", "anthropic/claude-opus-4-5"),
        ("ollama/qwen-2.5-coder", "ollama/qwen-2.5-coder"),
    ],
)
def test_resolve_model(value, expected):
    assert resolve_model(value) == expected


@pytest.mark.parametrize("value", ["qwen-2.5-coder", "claud-opus-4-5", "llama-3.3-70b"])
def test_resolve_model_rejects_ambiguous_bare_names(value):
    """A name no brand prefix claims is genuinely ambiguous — it must not fall
    through to a default harness, and a typo must not become a different model."""
    with pytest.raises(ValueError, match="write it as vendor/"):
        resolve_model(value)


def test_vendor_of():
    assert vendor_of("anthropic/claude-opus-4-5") == "anthropic"


def test_unnamed_harness_is_pi():
    assert harness_by_name(None) is DEFAULT_HARNESS
    assert harness_by_name(None).name == "pi"


def test_harness_by_name():
    assert harness_by_name("claude-code").name == "claude-code"


def test_unknown_harness_lists_the_choices():
    with pytest.raises(ValueError, match="unknown harness 'nope'. choices: claude-code, codex, pi"):
        harness_by_name("nope")


@pytest.mark.parametrize("vendor", ["anthropic", "openai", "ollama", "anything-else"])
def test_pi_serves_any_vendor(vendor):
    """pi never enumerates vendors — adding fireworks or ollama needs no code change."""
    assert PiHarness().serves(vendor)


@pytest.mark.parametrize(
    "vendor,expected", [("anthropic", True), ("openai", False), ("ollama", False)]
)
def test_claude_serves_only_anthropic(vendor, expected):
    assert ClaudeCodeHarness().serves(vendor) is expected


def test_harnesses_declare_the_infra_the_cli_needs():
    pi = PiHarness()
    assert (pi.state_path, pi.ports, pi.env) == (
        "~/.pi/agent",
        ("53692:53692",),
        {"PI_OAUTH_CALLBACK_HOST": "0.0.0.0"},
    )


def test_codex_is_registered():
    assert harness_by_name("codex").name == "codex"


@pytest.mark.parametrize(
    "vendor,expected",
    [("openai", True), ("anthropic", False), ("ollama", False), ("google", False)],
)
def test_codex_serves_only_openai(vendor, expected):
    """A vendor codex wrongly claimed would route that model's steps to it."""
    assert CodexHarness().serves(vendor) is expected


def test_every_harness_can_be_prepared(tmp_path, monkeypatch):
    """`prepare` is a customization point every harness has, not a hook one of
    them needed — give'r calls it without knowing which harness it holds."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for harness in HARNESSES:
        harness.prepare()


def test_every_harness_declares_whether_it_can_fork():
    """Branching is what makes a replayed step safe, and it is not universal —
    codex's headless resume continues a thread in place."""
    assert {h.name: h.forks_on_resume for h in HARNESSES} == {
        "claude-code": True,
        "codex": False,
        "pi": True,
    }


def test_every_harness_declares_how_it_is_installed():
    assert {h.name: h.install for h in HARNESSES} == {
        "claude-code": "npm install -g @anthropic-ai/claude-code",
        "codex": "npm install -g @openai/codex",
        "pi": "npm install -g --ignore-scripts @earendil-works/pi-coding-agent",
    }


def test_harnesses_sharing_a_prerequisite_share_the_constant():
    """`pre_install` is deduplicated by string equality, so three harnesses that
    each need node collapse to one install only if they name the same object.
    Copying the command into each class would silently install node twice."""
    assert {h.name: h.pre_install for h in HARNESSES} == {
        "claude-code": (NODE,),
        "codex": (NODE,),
        "pi": (NODE,),
    }


def test_a_preinstall_is_the_command_not_a_name_to_look_up():
    """Nothing resolves `pre_install` — a harness needing something other than
    node declares its own command and no code learns about it."""
    assert NODE.startswith("apt-get update")
    assert "nodejs" in NODE
