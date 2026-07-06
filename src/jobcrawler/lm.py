"""Construct ``dspy.LM``s, routing ChatGPT-subscription (Codex OAuth) slugs (§9).

Most deployments name an ``openrouter/<vendor>/<model>`` slug and bill per token. A
``chatgpt/<model>`` slug instead routes through litellm's native ChatGPT provider — the
Codex *Responses* backend billed against a ChatGPT subscription (see :mod:`jobcrawler.auth`
for the OAuth token it reads). That backend needs two things a plain ``dspy.LM(...)`` call
gets wrong:

  1. ``model_type="responses"`` — it speaks the Responses API, not chat completions.
  2. **No** ``temperature`` / ``max_tokens`` — the Codex backend ignores sampling params
     (litellm strips everything but a fixed allow-list), and worse, dspy's reasoning-model
     guard *raises* if a ``gpt-5*`` slug is handed ``temperature != 1`` or
     ``max_tokens < 16000`` (``clients/lm.py`` in the pinned 3.2.1). The §4.7 matcher builds
     its LM at ``temperature=0, max_tokens=4000`` — exactly the shape that trips the guard —
     so those kwargs must be dropped for a ``chatgpt/*`` slug.

Every LM construction site (generation root/sub, GEPA reflection, the matcher) goes through
:func:`build_lm` so this routing lives in one place; a non-``chatgpt`` slug is constructed
exactly as before, preserving the OpenRouter behavior byte-for-byte.

``dspy`` is imported lazily inside :func:`build_lm` (never at module load) so
:func:`is_chatgpt` stays dependency-free — the run/crawl startup preflight imports it to
decide whether a token check is even needed, and must not pull dspy to do so.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import dspy

# The provider prefix litellm routes to its native ChatGPT (Codex OAuth) backend.
CHATGPT_PREFIX = "chatgpt/"


def is_chatgpt(model: str) -> bool:
    """True if ``model`` routes through litellm's ChatGPT (Codex OAuth) provider (§9)."""
    return model.startswith(CHATGPT_PREFIX)


def build_lm(
    model: str,
    *,
    cls: type[dspy.LM] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> dspy.LM:
    """Build ``cls(model, ...)``, routing a ``chatgpt/*`` slug to the Codex backend (§9).

    For a ``chatgpt/*`` slug: force ``model_type="responses"`` and DROP
    ``temperature``/``max_tokens`` (the backend ignores them and dspy's gpt-5 guard rejects
    them), and pin the OAuth token env so litellm's provider finds the token ``jobcrawl
    login`` imported. For any other slug: pass ``temperature``/``max_tokens`` straight
    through, so the OpenRouter path is unchanged. ``cls`` (default ``dspy.LM``) lets a caller
    build a subclass — the §4.2 :class:`~jobcrawler.generate.budget.CappedLM` — with the same
    routing; extra ``kwargs`` (e.g. ``max_subcalls``) pass through to the constructor.
    """
    import dspy

    if cls is None:
        cls = dspy.LM

    if is_chatgpt(model):
        # Lazy import: only pull the auth module (and, through it, litellm) when a
        # chatgpt slug is actually in play. Pinning CHATGPT_TOKEN_DIR before the LM is
        # built is what makes litellm's provider read the store `jobcrawl login` wrote.
        from jobcrawler.auth import apply_auth_env

        apply_auth_env()
        kwargs.setdefault("model_type", "responses")
        return cls(model, **kwargs)

    # Non-chatgpt (the OpenRouter default): preserve the exact prior construction —
    # 0.0 is a meaningful temperature, so guard on `is not None`, not truthiness.
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return cls(model, **kwargs)
