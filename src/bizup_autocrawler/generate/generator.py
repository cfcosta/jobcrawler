"""The CrawlerGenerator module (§4.2, §4.11).

Wraps ``dspy.RLM`` (the primary generator) together with the narrow strategy
predictor we own. The strategy is held as an attribute so it auto-registers as a
parameter — visible to GEPA via ``named_predictors()`` and to ``save``/``load``.
GEPA is pointed *only* at ``strategy.predict`` (the ``site_features ->
discovery_plan`` predicate), never RLM's generic action loop, for §8
blast-radius control. RLM's interpreter is injectable: ``None`` in production
(the default Pyodide/Deno sandbox), a deterministic ``CodeInterpreter`` in tests.
Construction is Deno-free; only ``aforward`` with the default interpreter needs it.

The §6 N-candidate selection (BestOfN/Refine over the scoring core) lives in
``select.py`` — it depends on ``validate_crawler`` running a candidate, which is
the §4.4 sandbox. This module is just the one-candidate generation primitive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

import dspy
from dspy.primitives import CodeInterpreter

from bizup_autocrawler import config
from bizup_autocrawler.generate.signatures import DiscoveryStrategy, GenerateCrawler


class CrawlerGenerator(dspy.Module):
    def __init__(
        self,
        *,
        tools: list[Callable[..., object]] | None = None,
        interpreter: CodeInterpreter | None = None,
        sub_lm: dspy.LM | None = None,
        max_iterations: int | None = None,
        max_llm_calls: int | None = None,
    ) -> None:
        super().__init__()
        self.strategy = dspy.ChainOfThought(DiscoveryStrategy)
        self.rlm = dspy.RLM(
            GenerateCrawler,
            tools=tools or [],
            sub_lm=sub_lm,
            interpreter=interpreter,
            max_iterations=(
                config.max_iterations() if max_iterations is None else max_iterations
            ),
            max_llm_calls=(
                config.max_llm_calls() if max_llm_calls is None else max_llm_calls
            ),
        )

    async def aforward(
        self,
        *,
        site_url: str,
        corpus: Mapping[str, object],
        render: str = "auto",
        hints: str = "",
    ) -> dspy.Prediction:
        """Generate one crawler candidate: run the owned strategy, then the RLM."""
        plan = await self.strategy.acall(site_features=site_features(corpus))
        return await self.rlm.acall(
            site_url=site_url,
            corpus=corpus,
            render=render,
            hints=_steer(hints, plan.discovery_plan),
        )


async def select_best(
    module: dspy.Module,
    inputs: Mapping[str, object],
    *,
    reward_fn: Callable[[Mapping[str, object], dspy.Prediction], float],
    n: int,
    threshold: float = 1.0,
) -> dspy.Prediction:
    """§6 candidate selection: argmax over the graded scalar, early-breaking.

    Runs ``module`` up to ``n`` times with distinct ``rollout_id`` at
    temperature=1.0 (the real distinctness lever — not ``cache=False``), keeping
    the best by ``reward_fn`` and breaking the moment one clears ``threshold``
    (the win is the early break, not parallelism — BestOfN is a sequential
    for-loop). BestOfN is sync-only (§4.11), so it runs off the event loop to
    avoid blocking the §4.9 single-process scheduler. dspy's LM context does not
    cross into the executor thread, so the active LM is captured here and
    re-established inside the worker.
    """
    lm = dspy.settings.lm

    def run() -> dspy.Prediction:
        chooser = dspy.BestOfN(
            module=module, N=n, reward_fn=reward_fn, threshold=threshold
        )
        with dspy.context(lm=lm):
            return chooser(**inputs)

    return await asyncio.to_thread(run)


async def best_of_n(
    make_attempt: Callable[[int], "Awaitable[object]"],
    *,
    reward: Callable[[object], float],
    n: int,
    threshold: float = 1.0,
) -> tuple[object, float]:
    """§6 sequential best-of-N for the async generator: argmax over the scalar.

    ``make_attempt(i)`` produces candidate *i* (the caller varies the rollout_id
    at temperature=1.0, the real distinctness lever — §4.2). Keeps the best by
    ``reward`` and early-breaks the moment one clears ``threshold`` (the win is
    the break, not parallelism). Unlike ``select_best`` (stock sync ``BestOfN``),
    this drives an ``aforward``-only module directly — the §4.11 async caveat.
    """
    best: object = None
    best_reward = float("-inf")
    for i in range(n):
        pred = await make_attempt(i)
        value = reward(pred)
        if value > best_reward:
            best, best_reward = pred, value
        if value >= threshold:
            break
    return best, best_reward


def _steer(hints: str, discovery_plan: str) -> str:
    plan = f"Discovery plan:\n{discovery_plan}"
    return f"{hints}\n\n{plan}" if hints else plan


def site_features(corpus: Mapping[str, object]) -> str:
    """Per-site facts for the strategy predictor — to reason FROM, not memorize.

    Deliberately a summary (counts + a few URLs), not the raw feed URLs, so GEPA
    evolves generalizable strategy instead of hard-coding this site's endpoints.
    """
    articles = corpus.get("articles") or []
    lines = [
        f"articles_in_corpus: {len(articles) if isinstance(articles, list) else 0}",
        f"has_sitemap: {bool(corpus.get('sitemap'))}",
        f"robots_present: {bool(corpus.get('robots'))}",
    ]
    if isinstance(articles, list):
        lines.extend(
            f"article_url: {a.get('url', '')}"
            for a in articles[:5]
            if isinstance(a, Mapping)
        )
    return "\n".join(lines)
