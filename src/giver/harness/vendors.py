"""Resolving a model string to the vendor that serves it."""

# Brand prefixes, not model names: vendors ship new models far more often than
# they ship a new brand. A name no prefix claims stays ambiguous on purpose —
# `qwen-2.5-coder` is served by ollama, together and fireworks with different
# credentials and, locally, no cost at all.
VENDOR_PREFIXES = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
}


def _claims(prefix: str, model: str) -> bool:
    """A prefix claims a model when it is the whole name or the segment before a
    dash. `claude` claims `claude-opus-4-5`, `o3` claims both `o3` and `o3-mini`,
    and neither claims `clauderoo-1`."""
    return model == prefix or model.startswith(f"{prefix}-")


def resolve_model(value: str) -> str:
    """Canonicalize a model to `vendor/model`.

    A qualified value passes through unvalidated — vendors add models faster
    than give'r updates, and the harness itself rejects a bad id with a real
    error. A bare name resolves by brand prefix, or raises.
    """
    if "/" in value:
        return value
    for prefix, vendor in VENDOR_PREFIXES.items():
        if _claims(prefix, value):
            return f"{vendor}/{value}"
    raise ValueError(
        f"cannot infer a vendor for model {value!r} — write it as vendor/{value}"
    )


def vendor_of(model: str) -> str:
    return model.split("/", 1)[0]
