"""Tests for the CrawlerGenerator module (§4.2, §4.11).

Run the **real** RLM loop with no Deno and no real model: a DummyLM answers the
strategy + action calls, a deterministic MockInterpreter stands in for Pyodide.
The assertions pin that the genuine machinery ran — the strategy predictor is
GEPA-visible and save/load round-trips its instruction (the optimizer's target),
and aforward drives the injected interpreter and returns the typed outputs.
"""

from __future__ import annotations

from pathlib import Path

import dspy
import pytest
from dspy.primitives import FinalOutput
from dspy.utils import DummyLM
from pytest import MonkeyPatch

from bizup_autocrawler import config, generator_pointer
from bizup_autocrawler.generate.generator import (
    CrawlerGenerator,
    best_of_n,
    load_generator,
    read_generator_meta,
    save_generator,
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


def test_site_features_surfaces_the_enumeration_target() -> None:
    # The generator is told how many recent articles the site has, so it reasons
    # toward enumerating them instead of settling for a feed-sized sample (§6).
    features = site_features(build_corpus(homepage="h", expected_recent=42))
    assert "expected_recent_articles: 42" in features


def test_site_features_flags_a_structured_content_api() -> None:
    # A WordPress REST API (full bodies, paginated) is the best source — the strategy
    # is told it exists so it prefers it over feed/sitemap/scrape (§4.2).
    features = site_features(
        build_corpus(homepage="h", apis=["https://x.com/wp-json/wp/v2/posts"])
    )
    assert "structured_content_api: True" in features


def test_forward_bridges_to_aforward_for_the_optimizers() -> None:
    # dspy optimizers call modules synchronously; forward must run the same RLM loop
    # as aforward (off the event loop) and return the typed prediction (§4.11).
    interp = MockInterpreter([_final()])
    gen = CrawlerGenerator(interpreter=interp, max_iterations=5, max_llm_calls=5)
    lm = DummyLM(
        [
            {"reasoning": "a feed exists", "discovery_plan": "use the /feed"},
            {"reasoning": "submit", "code": "SUBMIT('done')"},
        ]
    )

    with dspy.context(lm=lm):
        pred = gen(
            site_url="https://example.com",
            corpus=build_corpus(homepage="<html/>"),
            render="auto",
            hints="",
        )

    assert pred.discovery_method == "feed"
    assert pred.discovery_url == "https://example.com/feed"


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


async def test_best_of_n_stops_at_the_fanout_spend_cap() -> None:
    # The §4.2 aggregate spend cap bounds the fan-out: at $3/candidate with a $5 cap,
    # the loop stops after the candidate that crosses it — never launching the rest.
    seen: list[int] = []

    async def make_attempt(i: int) -> object:
        seen.append(i)
        return i

    best, value = await best_of_n(
        make_attempt,
        reward=lambda p: 0.1,  # never clears the threshold
        n=10,
        threshold=1.0,
        cost=lambda p: 3.0,
        spend_cap=5.0,
    )
    assert seen == [0, 1]  # $3 then $6 ≥ $5 → stop, candidates 2..9 never run
    assert best == 0  # constant reward → the first candidate stays the argmax


async def test_best_of_n_without_spend_cap_runs_all_candidates() -> None:
    # Cost accounting is opt-in: absent a cap, prior behavior is unchanged.
    seen: list[int] = []

    async def make_attempt(i: int) -> object:
        seen.append(i)
        return i

    await best_of_n(make_attempt, reward=lambda p: 0.1, n=4, threshold=1.0)
    assert seen == [0, 1, 2, 3]


# -- §4.11 persistence: save / boot-load / rollback --------------------------

_OPTIMIZED = "OPTIMIZED DISCOVERY RULES"


def _optimized_generator(instruction: str) -> CrawlerGenerator:
    gen = CrawlerGenerator()
    sig = gen.strategy.predict.signature
    assert sig is not None
    gen.strategy.predict.signature = sig.with_instructions(instruction)
    return gen


def _instruction_of(gen: CrawlerGenerator) -> str:
    sig = gen.strategy.predict.signature
    assert sig is not None
    return sig.instructions


def test_save_generator_round_trips_the_optimized_instruction(tmp_path: Path) -> None:
    path = tmp_path / "generator.json"
    save_generator(_optimized_generator(_OPTIMIZED), path)

    reloaded = load_generator(path)

    # Identical program state ⇒ identical held-out score under any deterministic
    # re-score; the instruction is the strategy predictor's whole optimized payload.
    assert _OPTIMIZED in _instruction_of(reloaded)


def test_save_generator_refuses_a_non_json_suffix(tmp_path: Path) -> None:
    # save() dispatches on suffix: a .pkl cloudpickles even with save_program=False,
    # which is arbitrary-code-exec on load (§8). The helper refuses it outright.
    with pytest.raises(ValueError, match="json"):
        save_generator(CrawlerGenerator(), tmp_path / "generator.pkl")


def test_save_generator_writes_a_provenance_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "generator.json"
    save_generator(
        _optimized_generator(_OPTIMIZED),
        path,
        trainset_snapshot=7,
        heldout_score=0.83,
    )

    meta = read_generator_meta(path)
    assert meta["trainset_snapshot"] == 7
    assert meta["heldout_score"] == 0.83


def test_load_generator_falls_back_to_baseline_when_absent(tmp_path: Path) -> None:
    gen = load_generator(tmp_path / "missing.json")

    # A real, usable baseline — GEPA-visible predictor, default (un-optimized) rules.
    assert "strategy.predict" in {name for name, _ in gen.named_predictors()}
    assert _OPTIMIZED not in _instruction_of(gen)


def test_load_generator_recovers_from_a_corrupt_artifact(tmp_path: Path) -> None:
    path = tmp_path / "generator.json"
    path.write_text("{ not valid json")

    gen = load_generator(path)  # must not raise — the service has to boot

    assert _OPTIMIZED not in _instruction_of(gen)  # degraded to baseline


def test_rollback_flips_the_config_pointer_to_the_prior_artifact(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    prior = tmp_path / "generator-1.json"
    current = tmp_path / "generator-2.json"
    save_generator(_optimized_generator("PRIOR RULES"), prior)
    save_generator(_optimized_generator("REGRESSED RULES"), current)

    # The single config pointer selects the active artifact (§4.11)...
    monkeypatch.setenv("AUTOCRAWLER_GENERATOR_PATH", str(current))
    assert "REGRESSED RULES" in _instruction_of(load_generator())

    # ...and rollback is just flipping it back to the kept prior generator.json.
    monkeypatch.setenv("AUTOCRAWLER_GENERATOR_PATH", str(prior))
    assert "PRIOR RULES" in _instruction_of(load_generator())


def test_load_generator_prefers_the_pointer_over_the_env_var(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # Resolution order is arg > pointer > env > baseline: an adopted generator (pointer)
    # must win over the static deploy default (env), so a live daemon picks up an
    # auto-adoption without an env change/restart (§4.11).
    adopted = tmp_path / "generator-2.json"
    deploy_default = tmp_path / "generator-1.json"
    save_generator(_optimized_generator("ADOPTED RULES"), adopted)
    save_generator(_optimized_generator("DEPLOY DEFAULT RULES"), deploy_default)
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    monkeypatch.setenv("AUTOCRAWLER_GENERATOR_PATH", str(deploy_default))
    generator_pointer.set_active(str(adopted))

    assert "ADOPTED RULES" in _instruction_of(load_generator())


def test_load_generator_uses_the_env_var_when_no_pointer_is_set(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # No pointer (an existing deploy that never adopted) ⇒ the env var is the fallback,
    # so systemd units that only set AUTOCRAWLER_GENERATOR_PATH still boot from it.
    deploy_default = tmp_path / "generator-1.json"
    save_generator(_optimized_generator("DEPLOY DEFAULT RULES"), deploy_default)
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    monkeypatch.setenv("AUTOCRAWLER_GENERATOR_PATH", str(deploy_default))
    assert generator_pointer.active_generator_path() is None

    assert "DEPLOY DEFAULT RULES" in _instruction_of(load_generator())


def test_load_generator_explicit_arg_beats_pointer_and_env(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # An explicit path arg (the optimize job re-scoring a specific artifact) is the
    # top of the resolution order — neither the pointer nor the env can override it.
    explicit = tmp_path / "generator-3.json"
    adopted = tmp_path / "generator-2.json"
    deploy_default = tmp_path / "generator-1.json"
    save_generator(_optimized_generator("EXPLICIT RULES"), explicit)
    save_generator(_optimized_generator("ADOPTED RULES"), adopted)
    save_generator(_optimized_generator("DEPLOY DEFAULT RULES"), deploy_default)
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    monkeypatch.setenv("AUTOCRAWLER_GENERATOR_PATH", str(deploy_default))
    generator_pointer.set_active(str(adopted))

    assert "EXPLICIT RULES" in _instruction_of(load_generator(explicit))


class _RecordingRLM:
    """Stand-in for the RLM that records the hints it was steered with."""

    def __init__(self) -> None:
        self.hints: str | None = None

    async def acall(self, **kwargs: object) -> dspy.Prediction:
        hints = kwargs.get("hints")
        self.hints = hints if isinstance(hints, str) else None
        return dspy.Prediction(
            discovery_method="feed", discovery_url="u", crawler_source=_CRAWLER
        )


async def _hints_for_plan(plan_text: str, monkeypatch: MonkeyPatch) -> str:
    gen = CrawlerGenerator()
    recorder = _RecordingRLM()
    monkeypatch.setattr(gen, "rlm", recorder)
    with dspy.context(lm=DummyLM([{"reasoning": "r", "discovery_plan": plan_text}])):
        await gen.aforward(
            site_url="https://example.com",
            corpus=build_corpus(homepage="<html/>"),
            render="auto",
            hints="",
        )
    assert recorder.hints is not None
    return recorder.hints


async def test_strategy_output_is_load_bearing_in_generation(
    monkeypatch: MonkeyPatch,
) -> None:
    # The §4.11 predictor-is-load-bearing guarantee: a no-op predictor would round-
    # trip through JSON yet steer nothing. Pin *where* discovery_plan enters the
    # GenerateCrawler prompt — divergent plans must produce divergent RLM hints.
    alpha = await _hints_for_plan("PLAN-ALPHA", monkeypatch)
    beta = await _hints_for_plan("PLAN-BETA", monkeypatch)
    assert "PLAN-ALPHA" in alpha and "PLAN-BETA" in beta
    assert alpha != beta
