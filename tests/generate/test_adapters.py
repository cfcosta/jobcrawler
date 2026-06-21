"""Tests for the three dspy adapters over one scoring core (§4.11).

The metric is the unified Evaluate/GEPA callable (returns Prediction(score,
feedback)); reward_fn is the BestOfN/Refine outlier (returns a bare float). Both
sit over an *injected* core so the deterministic wiring — AST short-circuit,
forced-extraction penalty, what-failed feedback, Evaluate's float coercion —
is testable without a live sandbox or LM. The core is a stub returning a canned
CandidateResult.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bizup_autocrawler.generate.scoring import (
    FORCED_EXTRACTION_PENALTY,
    FORCED_FINAL_REASONING,
    CandidateResult,
    ScoringCore,
    make_metric,
    make_reward_fn,
)

_GOOD_SOURCE = 'SITE = "https://example.com"\ndef crawl(ctx):\n    yield None\n'


def _sample(url: str = "https://example.com/a") -> dict[str, str]:
    return {"url": url, "title": "A headline", "body": "x" * 600}


def _result(
    item_count: int = 30, samples: list[dict[str, str]] | None = None
) -> CandidateResult:
    return CandidateResult(
        item_count=item_count,
        samples=samples if samples is not None else [_sample()],
        pages_fetched=1,
    )


def _core(result: CandidateResult) -> ScoringCore:
    def core(source: str, carrier: object) -> CandidateResult:
        return result

    return core


def _exploding_core() -> ScoringCore:
    def core(source: str, carrier: object) -> CandidateResult:
        raise AssertionError("core must not run when AST gate fails")

    return core


def _pred(
    source: str = _GOOD_SOURCE,
    *,
    discovery_method: str = "feed",
    final_reasoning: str = "done",
) -> SimpleNamespace:
    return SimpleNamespace(
        crawler_source=source,
        discovery_method=discovery_method,
        final_reasoning=final_reasoning,
    )


def _gold(site_url: str = "https://example.com") -> SimpleNamespace:
    return SimpleNamespace(site_url=site_url, corpus={})


def _metric(core: ScoringCore):
    return make_metric(core, target_n=30, min_items=5, min_body_chars=400)


def test_metric_returns_prediction_score_and_feedback() -> None:
    out = _metric(_core(_result()))(_gold(), _pred())
    assert out.score == pytest.approx(1.0)
    assert out.feedback == ""  # nothing failed completeness


def test_metric_ast_failure_short_circuits_without_running_core() -> None:
    bad = 'import os\nSITE="https://example.com"\ndef crawl(ctx):\n    yield 1\n'
    out = _metric(_exploding_core())(_gold(), _pred(bad))
    assert out.score == 0.0
    assert "os" in out.feedback


def test_metric_completeness_failure_feeds_back_what_failed() -> None:
    out = _metric(_core(_result(item_count=1)))(_gold(), _pred())
    assert out.score < 1.0
    assert "items" in out.feedback


def test_metric_is_float_coercible_for_evaluate() -> None:
    # dspy.Evaluate sums the metric return; Prediction.__float__ reads .score.
    out = _metric(_core(_result()))(_gold(), _pred())
    assert float(out) == pytest.approx(1.0)
    assert sum([out, out]) == pytest.approx(2.0)


def test_metric_accepts_gepa_five_arg_call() -> None:
    out = _metric(_core(_result()))(_gold(), _pred(), None, "strategy_predictor", None)
    assert out.score == pytest.approx(1.0)


def test_metric_reads_dspy_code_like_source() -> None:
    code_like = SimpleNamespace(code=_GOOD_SOURCE)
    pred = _pred()
    pred.crawler_source = code_like
    out = _metric(_core(_result()))(_gold(), pred)
    assert out.score == pytest.approx(1.0)


def test_reward_fn_returns_bare_float() -> None:
    reward = make_reward_fn(_core(_result()), target_n=30, min_body_chars=400)
    value = reward({"site_url": "https://example.com"}, _pred())
    assert isinstance(value, float)
    assert value == pytest.approx(1.0)


def test_reward_fn_applies_forced_extraction_penalty() -> None:
    reward = make_reward_fn(_core(_result()), target_n=30, min_body_chars=400)
    clean = reward({"site_url": "https://example.com"}, _pred())
    forced = reward(
        {"site_url": "https://example.com"},
        _pred(final_reasoning=FORCED_FINAL_REASONING),
    )
    assert forced == pytest.approx(clean - FORCED_EXTRACTION_PENALTY)


def test_reward_fn_vetoes_off_domain_items() -> None:
    reward = make_reward_fn(
        _core(_result(item_count=100, samples=[_sample("https://evil.com/x")])),
        target_n=30,
        min_body_chars=400,
    )
    assert reward({"site_url": "https://example.com"}, _pred()) == 0.0
