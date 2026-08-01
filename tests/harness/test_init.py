import pytest

from giver.harness import (
    DEFAULT_HARNESS,
    ClaudeHarness,
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
    assert harness_by_name("claude").name == "claude"


def test_unknown_harness_lists_the_choices():
    with pytest.raises(ValueError, match="unknown harness 'nope'. choices: claude, codex, pi"):
        harness_by_name("nope")


@pytest.mark.parametrize("vendor", ["anthropic", "openai", "ollama", "anything-else"])
def test_pi_serves_any_vendor(vendor):
    """pi never enumerates vendors — adding fireworks or ollama needs no code change."""
    assert PiHarness().serves(vendor)


@pytest.mark.parametrize(
    "vendor,expected", [("anthropic", True), ("openai", False), ("ollama", False)]
)
def test_claude_serves_only_anthropic(vendor, expected):
    assert ClaudeHarness().serves(vendor) is expected


def test_harnesses_declare_the_infra_the_cli_needs():
    pi = PiHarness()
    assert (pi.state_path, pi.ports, pi.env) == (
        "~/.pi/agent",
        ("53692:53692",),
        {"PI_OAUTH_CALLBACK_HOST": "0.0.0.0"},
    )


def test_codex_is_registered_and_serves_openai():
    from giver.harness import CodexHarness

    assert harness_by_name("codex").name == "codex"
    assert CodexHarness().serves("openai") and not CodexHarness().serves("anthropic")


def test_every_harness_declares_whether_it_can_fork():
    """Forking is what makes a replayed step idempotent, and it is not
    universal — codex's headless resume continues a thread in place."""
    from giver.harness import HARNESSES

    assert {h.name: h.forks_on_resume for h in HARNESSES} == {
        "claude": True,
        "codex": False,
        "pi": True,
    }


def test_the_default_harness_is_named_not_positional():
    from giver.harness import DEFAULT_HARNESS_NAME

    assert DEFAULT_HARNESS_NAME == "pi"
    assert harness_by_name(None).name == DEFAULT_HARNESS_NAME
