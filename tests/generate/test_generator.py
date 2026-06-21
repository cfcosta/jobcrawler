"""Tests for the CrawlerGenerator module (§4.2, §4.11).

Run the **real** RLM loop with no Deno and no real model: a DummyLM answers the
strategy + action calls, a deterministic MockInterpreter stands in for Pyodide.
The assertions pin that the genuine machinery ran — the strategy predictor is
GEPA-visible and save/load round-trips its instruction (the optimizer's target),
and aforward drives the injected interpreter and returns the typed outputs.
"""

from __future__ import annotations

import dspy
from dspy.primitives import FinalOutput
from dspy.utils import DummyLM

from bizup_autocrawler.generate.generator import (
    CrawlerGenerator,
    best_of_n,
    select_best,
    site_features,
)
from bizup_autocrawler.generate.snapshot import build_corpus
from tests.generate._mock_interpreter import MockInterpreter

_CRAWLER = 'SITE = "https://example.com"\ndef crawl(ctx):\n    yield None\n'


def _final() -> FinalOutput:
    return FinalOutput(
        {
            "discovery_method": "feed",
            "discovery_url": "https://example.com/feed",
            "crawler_source": _CRAWLER,
        }
    )


def test_strategy_predictor_is_gepa_visible() -> None:
    gen = CrawlerGenerator()
    assert "strategy.predict" in {name for name, _ in gen.named_predictors()}


def test_save_load_round_trips_the_strategy_instruction(tmp_path: object) -> None:
    gen = CrawlerGenerator()
    sig = gen.strategy.predict.signature
    assert sig is not None
    gen.strategy.predict.signature = sig.with_instructions("OPTIMIZED RULES")
    path = f"{tmp_path}/generator.json"  # type: ignore[str-bytes-safe]

    gen.save(path, save_program=False)
    reloaded = CrawlerGenerator()
    reloaded.load(path)
    reloaded_sig = reloaded.strategy.predict.signature
    assert reloaded_sig is not None
    assert "OPTIMIZED RULES" in reloaded_sig.instructions


def test_site_features_summarizes_without_leaking_feed_urls() -> None:
    corpus = build_corpus(
        homepage="h",
        sitemap="<urlset/>",
        articles=[{"url": "https://example.com/a", "title": "A", "text": "t"}],
    )
    features = site_features(corpus)
    assert "articles_in_corpus: 1" in features
    assert "has_sitemap: True" in features


async def test_aforward_generates_candidate_and_drives_interpreter() -> None:
    interp = MockInterpreter([_final()])
    gen = CrawlerGenerator(interpreter=interp, max_iterations=5, max_llm_calls=5)
    lm = DummyLM(
        [
            {"reasoning": "a feed exists", "discovery_plan": "use the /feed"},
            {"reasoning": "submit", "code": "SUBMIT('done')"},
        ]
    )

    with dspy.context(lm=lm):
        pred = await gen.aforward(
            site_url="https://example.com",
            corpus=build_corpus(homepage="<html/>"),
            render="auto",
            hints="",
        )

    assert pred.discovery_method == "feed"
    assert pred.discovery_url == "https://example.com/feed"
    source = getattr(pred.crawler_source, "code", None) or str(pred.crawler_source)
    assert "SITE" in source
    # honesty: the real RLM loop drove THIS interpreter before SUBMIT terminated it.
    assert len(interp.call_history) == 1
    assert "SUBMIT" in interp.call_history[0][0]
    assert "llm_query" in interp.tools


async def test_select_best_returns_argmax_over_reward() -> None:
    # A stub module that returns a prediction keyed on the rollout_id BestOfN sets,
    # so reward_fn can prefer one specific attempt. Proves the argmax wiring +
    # async offload without a real LM.
    class _Stub(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.predict = dspy.Predict("x -> y")  # a predictor to carry the rollout LM

        def forward(self, **kwargs: object) -> dspy.Prediction:
            lm = self.predict.lm
            rid = (lm.kwargs.get("rollout_id", 0) if lm else 0) or 0
            return dspy.Prediction(candidate=f"c{rid}", rid=rid)

    def reward_fn(kwargs: object, pred: dspy.Prediction) -> float:
        return float(pred.rid)  # prefers the highest rollout_id

    with dspy.context(lm=DummyLM([{"x": "y"}])):
        best = await select_best(
            _Stub(), {"site_url": "u"}, reward_fn=reward_fn, n=3, threshold=99.0
        )
    assert best.rid == 2  # best of rollout_ids 0,1,2 under an unreachable threshold


def _reward_int(pred: object) -> float:
    assert isinstance(pred, int)
    return float(pred)


async def test_best_of_n_picks_highest_reward() -> None:
    async def make_attempt(i: int) -> object:
        return i

    best, value = await best_of_n(make_attempt, reward=_reward_int, n=3, threshold=99.0)
    assert best == 2 and value == 2.0  # unreachable threshold → argmax over all 3


async def test_best_of_n_early_breaks_on_threshold() -> None:
    seen: list[int] = []

    async def make_attempt(i: int) -> object:
        seen.append(i)
        return i

    best, value = await best_of_n(make_attempt, reward=_reward_int, n=5, threshold=1.0)
    assert best == 1 and value == 1.0
    assert seen == [0, 1]  # stopped the moment one cleared the threshold


async def test_best_of_n_skips_a_failing_attempt() -> None:
    async def make_attempt(i: int) -> object:
        if i == 0:
            raise RuntimeError("rollout 0 blew up")
        return i

    best, value = await best_of_n(make_attempt, reward=_reward_int, n=3, threshold=99.0)
    assert best == 2 and value == 2.0  # the failed attempt is skipped, not fatal
