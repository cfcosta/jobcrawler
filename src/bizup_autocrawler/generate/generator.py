"""The CrawlerGenerator module (§4.2, §4.11).

Wraps ``dspy.RLM`` (the primary generator) together with the narrow strategy
predictor we own. The strategy is held as an attribute so it auto-registers as a
parameter — visible to GEPA via ``named_predictors()`` and to ``save``/``load``.
GEPA is pointed *only* at ``strategy.predict`` (the ``site_features ->
discovery_plan`` predicate), never RLM's generic action loop, for §8
blast-radius control. RLM's interpreter is injectable: ``None`` in production
(the default Pyodide/Deno sandbox), a deterministic ``CodeInterpreter`` in tests.
Construction is Deno-free; only ``aforward`` with the default interpreter needs it.

``aforward`` is the one-candidate primitive; the §6 N-candidate selection over
the graded scalar is ``best_of_n`` (async, for this aforward-only module) and
``select_best`` (stock sync ``BestOfN``) below, scored via the §4.4 sandbox.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import dspy
from dspy.primitives import CodeInterpreter

from bizup_autocrawler import config
from bizup_autocrawler.generate.budget import reset_subcalls
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
        # Held for the §4.2 per-trajectory sub-call budget reset (a CappedLM in
        # production; duck-typed so a plain/fake sub_lm in tests is a no-op).
        self._sub_lm = sub_lm
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
        # One aforward is one trajectory: reset the host sub-call budget so the
        # ceiling bounds *this* generation, not the cumulative fan-out/batch (§4.2).
        reset_subcalls(self._sub_lm)
        plan = await self.strategy.acall(site_features=site_features(corpus))
        return await self.rlm.acall(
            site_url=site_url,
            corpus=corpus,
            render=render,
            hints=_steer(hints, plan.discovery_plan),
        )

    def forward(
        self,
        *,
        site_url: str,
        corpus: Mapping[str, object],
        render: str = "auto",
        hints: str = "",
    ) -> dspy.Prediction:
        """Sync entry point for the dspy optimizers (§4.11): BootstrapFewShot/GEPA
        call modules synchronously, so bridge to ``aforward``. The offline optimize
        job runs outside any event loop, so ``asyncio.run`` is safe here; the §4.9
        scheduler and §6 best-of-N drive ``aforward`` directly.
        """
        return asyncio.run(
            self.aforward(site_url=site_url, corpus=corpus, render=render, hints=hints)
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
    cost: Callable[[object], float] | None = None,
    spend_cap: float | None = None,
) -> tuple[object, float]:
    """§6 sequential best-of-N for the async generator: argmax over the scalar.

    ``make_attempt(i)`` produces candidate *i* (the caller varies the rollout_id
    at temperature=1.0, the real distinctness lever — §4.2). Keeps the best by
    ``reward`` and early-breaks the moment one clears ``threshold`` (the win is
    the break, not parallelism). Unlike ``select_best`` (stock sync ``BestOfN``),
    this drives an ``aforward``-only module directly — the §4.11 async caveat.

    ``cost`` + ``spend_cap`` enforce the §4.2 aggregate ``FANOUT_SPEND_CAP``: the
    cumulative ``cost(pred)`` is tracked and the fan-out stops once it reaches the
    cap (after keeping the best so far), so a long tail of expensive candidates
    can't run away. With no cap (or a cost that reports 0 — no usage signal), the
    fan-out is still bounded structurally by ``n`` and each candidate by the
    per-trajectory ``MAX_SUBCALLS`` kill.
    """
    best: object = None
    best_reward = float("-inf")
    spent = 0.0
    for i in range(n):
        try:
            pred = await make_attempt(i)
            value = reward(pred)
        except Exception as exc:  # one bad rollout must not abort selection (§4.4)
            print(
                f"[best_of_n] candidate {i} failed: {type(exc).__name__}: {str(exc)[:140]}"
            )
            continue
        if value > best_reward:
            best, best_reward = pred, value
        if value >= threshold:
            break
        if cost is not None and spend_cap is not None:
            spent += cost(pred)
            if spent >= spend_cap:
                print(
                    f"[best_of_n] fan-out spend cap reached: ${spent:.2f} >= "
                    f"${spend_cap:.2f} — stopping after {i + 1} candidate(s)"
                )
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
    feeds = corpus.get("feeds") or []
    apis = corpus.get("apis") or []
    expected = corpus.get("expected_recent")
    lines = [
        f"articles_in_corpus: {len(articles) if isinstance(articles, list) else 0}",
        f"feeds_found: {len(feeds) if isinstance(feeds, list) else 0}",
        # A structured content API (e.g. WordPress REST) returns full bodies as
        # paginated JSON — the best source; prefer it over feed/sitemap/scrape (§4.2).
        f"structured_content_api: {bool(apis)}",
        # The enumeration target: the plan must reach most of these, not a feed-sized
        # sample — a feed/homepage that surfaces far fewer is under-enumerating (§6).
        f"expected_recent_articles: {expected if isinstance(expected, int) else 0}",
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


# -- §4.11 persistence: JSON save / boot-load / rollback ---------------------


def _meta_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.meta.json")


def save_generator(
    generator: CrawlerGenerator,
    path: str | Path,
    *,
    trainset_snapshot: int = 0,
    heldout_score: float | None = None,
    created_at: str | None = None,
) -> Path:
    """Persist the optimized strategy as **JSON state** (§4.11), plus a provenance
    sidecar (``<name>.meta.json``) tagging the trainset snapshot and held-out score.

    Enforces the ``.json`` suffix: ``save()`` dispatches on suffix, so a ``.pkl``
    cloudpickles *even with* ``save_program=False`` — arbitrary-code-exec on load,
    contrary to §8. ``save_program=True`` is never passed, for the same reason. The
    DSPy/python versions are already pinned inside the artifact's own metadata; the
    caller owns versioning by choosing a ``generator-<version>.json`` name and
    keeping the prior file for rollback (a single config pointer selects the active).
    """
    p = Path(path)
    if p.suffix != ".json":
        raise ValueError(
            f"generator artifact must be a .json path (got {p.name!r}); a .pkl would "
            "cloudpickle even with save_program=False — §8 forbids that on load"
        )
    generator.save(str(p), save_program=False)
    _meta_path(p).write_text(
        json.dumps(
            {
                "trainset_snapshot": trainset_snapshot,
                "heldout_score": heldout_score,
                "created_at": created_at,
            },
            indent=2,
        )
    )
    return p


def read_generator_meta(path: str | Path) -> dict[str, object]:
    """Read the provenance sidecar from :func:`save_generator` (``{}`` if absent)."""
    meta = _meta_path(Path(path))
    if not meta.exists():
        return {}
    try:
        data = json.loads(meta.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_generator(
    path: str | Path | None = None,
    *,
    tools: list[Callable[..., object]] | None = None,
    interpreter: CodeInterpreter | None = None,
    sub_lm: dspy.LM | None = None,
    max_iterations: int | None = None,
    max_llm_calls: int | None = None,
) -> CrawlerGenerator:
    """Reconstruct ``CrawlerGenerator`` and ``.load()`` the optimized JSON state on
    boot (§4.11); fall back to a fresh **baseline** when the artifact is absent or
    unreadable, so the service always boots.

    ``path`` defaults to the single config pointer ``AUTOCRAWLER_GENERATOR_PATH`` —
    flipping that pointer to a prior ``generator.json`` *is* the rollback. The live
    wiring (tools / sub_lm / interpreter / caps) is passed through to the rebuilt
    module; only the declarative strategy *state* comes from the artifact.
    """

    def _build() -> CrawlerGenerator:
        return CrawlerGenerator(
            tools=tools,
            interpreter=interpreter,
            sub_lm=sub_lm,
            max_iterations=max_iterations,
            max_llm_calls=max_llm_calls,
        )

    target = config.generator_path() if path is None else str(path)
    generator = _build()
    if not target or not Path(target).exists():
        return generator  # cold start — no optimized artifact pinned yet
    try:
        generator.load(target)
    except Exception as exc:  # corrupt/incompatible artifact must not stop boot
        print(
            f"[load_generator] ignoring unreadable {target!r}: "
            f"{type(exc).__name__}: {str(exc)[:140]} — using baseline"
        )
        return _build()
    return generator
