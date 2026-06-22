"""The ``autocrawl optimize`` offline self-improvement job (§4.11) — out-of-band.

This is the manual surface that *closes* the §4.11 loop. It:

  1. builds the admitted trainset from the recorded generations — §6 gates + §8
     adversarial already passed at record time, so admission here is the per-site
     stability condition (a site kept only after enough sustained non-stale runs);
  2. splits the examples into disjoint train / val / held-out **by site**;
  3. runs the M5a bootstrap rung over the owned strategy predictor; and
  4. promotes the result only if it beats the incumbent on the **frozen held-out**
     (a pool the optimizer never saw) by the noise-floor margin — writing a
     versioned ``generator-N.json`` the operator points the config pointer at.

It is never the §4.9 scheduler: ``compile()`` (many live RLM rollouts) never runs
in-band. Heavy deps (dspy, the sandbox) are imported by the CLI lazily.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import dspy

from bizup_autocrawler import config
from bizup_autocrawler.generate.budget import CappedLM
from bizup_autocrawler.generate.generator import (
    CrawlerGenerator,
    load_generator,
    save_generator,
)
from bizup_autocrawler.generate.optimize import bootstrap_compile, run_optimization
from bizup_autocrawler.generate.scoring import (
    CandidateResult,
    make_bootstrap_metric,
    make_metric,
)
from bizup_autocrawler.generate.snapshot import prefetch_corpus
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.state import StateStore
from bizup_autocrawler.trainset import load_records, optimization_pool, split_by_site
from bizup_autocrawler.urls import normalize_host

_TARGET_ITEMS = 30  # the items-axis saturation target (mirrors cli_generate)
_MIN_SITES = 3  # need at least one site each for train / val / held-out


def optimize_command(args: argparse.Namespace) -> int:
    from bizup_autocrawler.cli import _db_path  # avoid an import cycle

    store = StateStore(_db_path())
    try:
        examples = optimization_pool(
            load_records(config.trainset_path()),
            stable_runs=lambda slug: _stable_runs(store, slug),
            required_stable=config.trainset_stable_runs(),
            prefetch=_prefetch_corpus,  # hard cases scored on the site's current state
        )
    finally:
        store.close()

    if len(examples) < _MIN_SITES:
        print(
            f"no admitted trainset yet: {len(examples)} site(s) cleared "
            f"{config.trainset_stable_runs()} stable run(s) — need >= {_MIN_SITES} "
            "for a disjoint train/val/held-out split (§4.11)"
        )
        return 0

    train, val, holdout = split_by_site(examples, holdout=1, val=1)
    result = _optimize(train, val, holdout)

    if result.get("promoted"):
        out = save_generator(
            cast("CrawlerGenerator", result["optimized_program"]),
            _next_generator_path(),
            trainset_snapshot=len(train),
            heldout_score=_as_float(result.get("optimized")),
        )
        print(
            f"PROMOTED: baseline={_as_float(result.get('baseline')):.1f} "
            f"optimized={_as_float(result.get('optimized')):.1f} "
            f"margin={_as_float(result.get('margin')):.1f}"
        )
        print(f"  wrote {out}")
        print(f"  deploy: set AUTOCRAWLER_GENERATOR_PATH={out}")
        return 0

    reason = result.get("reason") or "held-out did not beat the incumbent by the margin"
    print(f"not promoted: {reason}")
    return 0


def _optimize(
    train: list[dspy.Example],
    val: list[dspy.Example],
    holdout: list[dspy.Example],
) -> dict[str, object]:
    """The live optimize step — build the metrics + incumbent, then run_optimization."""
    max_tokens = config.max_tokens()
    root_lm = dspy.LM(config.model(), max_tokens=max_tokens)
    # Required cheap sub_lm + the §4.2 host sub-call kill (never the silent root
    # fallback, which bills every llm_query at planner rates).
    sub_lm = CappedLM(
        config.require_sub_model(),
        max_subcalls=config.max_subcalls(),
        max_tokens=max_tokens,
    )

    def core(source: str, carrier: object) -> CandidateResult:
        host = normalize_host(_carrier_site_url(carrier))
        return validate_crawler(source, allowed_hosts={host} if host else set())

    bootstrap_metric = make_bootstrap_metric(
        core, target_n=_TARGET_ITEMS, min_body_chars=config.min_body_chars()
    )
    eval_metric = make_metric(
        core,
        target_n=_TARGET_ITEMS,
        min_items=config.min_items(),
        min_body_chars=config.min_body_chars(),
    )
    # The incumbent: the pinned generator.json if any, else baseline (§4.11 boot-load).
    baseline = load_generator(
        sub_lm=sub_lm,
        max_iterations=config.max_iterations(),
        max_llm_calls=config.max_llm_calls(),
    )

    with dspy.context(lm=root_lm):
        # The minibatch the saturation guard reads: the incumbent's per-site scores.
        minibatch_scores = [
            bootstrap_metric(example, baseline(**example.inputs())) for example in train
        ]
        return run_optimization(
            baseline=baseline,
            optimized_factory=lambda: bootstrap_compile(
                baseline, trainset=train, valset=val, metric=bootstrap_metric
            ),
            trainset=train,
            valset=val,
            holdout=holdout,
            metric=eval_metric,
            minibatch_scores=minibatch_scores,
        )


def _prefetch_corpus(site_url: str) -> dict[str, object]:
    """Re-prefetch a hard case's corpus fresh, so the optimizer scores the strategy
    against the site's *current* (drifted) state (§4.11)."""
    host = normalize_host(site_url)
    return prefetch_corpus(site_url, Ctx({host} if host else set()))


def _stable_runs(store: StateStore, slug: str) -> int:
    """Consecutive most-recent *finished* runs that found items (non-stale, §4.8)."""
    count = 0
    for run in store.runs_for(slug):
        if run.finished is None:
            continue
        if run.items_found > 0:
            count += 1
        else:
            break
    return count


def _next_generator_path() -> str:
    directory = Path(config.generator_dir())
    directory.mkdir(parents=True, exist_ok=True)
    existing = list(directory.glob("generator-*.json"))
    return str(directory / f"generator-{len(existing) + 1}.json")


def _carrier_site_url(carrier: object) -> str:
    if isinstance(carrier, dspy.Example):
        return str(carrier.get("site_url", "") or "")
    return str(getattr(carrier, "site_url", "") or "")


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
