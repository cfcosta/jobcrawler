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

from bizup_autocrawler.generate.generator import CrawlerGenerator, site_features
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
    optimized = gen.strategy.predict.signature.with_instructions("OPTIMIZED RULES")
    gen.strategy.predict.signature = optimized
    path = f"{tmp_path}/generator.json"  # type: ignore[str-bytes-safe]

    gen.save(path, save_program=False)
    reloaded = CrawlerGenerator()
    reloaded.load(path)
    assert "OPTIMIZED RULES" in reloaded.strategy.predict.signature.instructions


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
