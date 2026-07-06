"""Tests for LM construction routing (§9).

Every LM in the system is built through :func:`jobcrawler.lm.build_lm`, which sends a
``chatgpt/*`` slug to litellm's ChatGPT (Codex OAuth) Responses backend and any other slug
down the unchanged OpenRouter path. The two behaviors that must hold: a ``chatgpt/*`` slug
gets ``model_type="responses"`` and has ``temperature``/``max_tokens`` DROPPED (the backend
ignores them and dspy's gpt-5 guard rejects them), while a non-``chatgpt`` slug keeps both.
These are asserted by capturing the constructor kwargs — no LM is ever really built.
"""

from __future__ import annotations

from typing import Any

import dspy
import pytest

from jobcrawler import config, lm
from jobcrawler.generate.budget import CappedLM


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every ``dspy.LM.__init__`` call's ``(model, kwargs)`` without building an LM.

    Patching the base ``__init__`` also captures :class:`CappedLM` construction, which chains
    through ``super().__init__`` — so the ``cls=CappedLM`` routing is exercised too.
    """
    calls: list[dict[str, Any]] = []
    orig = dspy.LM.__init__

    def spy(self: dspy.LM, model: str, **kwargs: Any) -> None:
        calls.append({"model": model, "kwargs": kwargs})
        orig(self, model, **kwargs)

    monkeypatch.setattr(dspy.LM, "__init__", spy)
    return calls


def test_is_chatgpt_matches_only_the_provider_prefix() -> None:
    assert lm.is_chatgpt("chatgpt/gpt-5-codex") is True
    assert lm.is_chatgpt("openrouter/openai/gpt-4o") is False
    assert lm.is_chatgpt("gpt-5-codex") is False  # bare slug is not the litellm route


def test_non_chatgpt_slug_keeps_temperature_and_max_tokens(
    captured: list[dict[str, Any]],
) -> None:
    # The OpenRouter path is byte-for-byte the prior behavior: both sampling params pass
    # through and no responses model_type is forced.
    lm.build_lm("openrouter/test/model", temperature=0.0, max_tokens=4000)
    (call,) = captured
    assert call["model"] == "openrouter/test/model"
    assert call["kwargs"]["temperature"] == 0.0
    assert call["kwargs"]["max_tokens"] == 4000
    assert "model_type" not in call["kwargs"]


def test_chatgpt_slug_forces_responses_and_drops_sampling_params(
    captured: list[dict[str, Any]],
) -> None:
    # The exact shape the §4.7 matcher passes (temperature=0, max_tokens=4000) — which would
    # trip dspy's gpt-5 guard — must come out the other side stripped, model_type=responses.
    lm.build_lm("chatgpt/gpt-5-codex", temperature=0.0, max_tokens=4000)
    (call,) = captured
    assert call["model"] == "chatgpt/gpt-5-codex"
    assert call["kwargs"]["model_type"] == "responses"
    assert "temperature" not in call["kwargs"]
    assert "max_tokens" not in call["kwargs"]


def test_chatgpt_slug_pins_the_token_dir_env(
    captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # Building a chatgpt LM must point litellm's provider at jobcrawler's token store so it
    # reads the credential `jobcrawl login` wrote — apply_auth_env sets CHATGPT_TOKEN_DIR.
    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", str(tmp_path))
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    lm.build_lm("chatgpt/gpt-5-codex")
    assert config.auth_dir() == str(tmp_path)


def test_capped_lm_class_routes_through_the_same_logic(
    captured: list[dict[str, Any]],
) -> None:
    # The §4.2 sub_lm is a CappedLM; build_lm must construct that subclass (not a plain LM)
    # while applying the chatgpt routing and forwarding max_subcalls to its constructor.
    built = lm.build_lm(
        "chatgpt/gpt-5-codex", cls=CappedLM, max_subcalls=3, max_tokens=16000
    )
    assert isinstance(built, CappedLM)
    (call,) = captured
    assert call["kwargs"]["model_type"] == "responses"
    assert "max_tokens" not in call["kwargs"]  # dropped even for the capped path
    # max_subcalls is consumed by CappedLM.__init__, never forwarded to the base LM.
    assert "max_subcalls" not in call["kwargs"]


def test_capped_lm_non_chatgpt_keeps_max_tokens(
    captured: list[dict[str, Any]],
) -> None:
    built = lm.build_lm(
        "openrouter/test/model", cls=CappedLM, max_subcalls=3, max_tokens=16000
    )
    assert isinstance(built, CappedLM)
    (call,) = captured
    assert call["kwargs"]["max_tokens"] == 16000
    assert "model_type" not in call["kwargs"]
