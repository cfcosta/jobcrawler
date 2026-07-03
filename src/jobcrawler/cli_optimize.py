"""The ``jobcrawl optimize`` offline self-improvement job (§4.11) — out-of-band.

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
from collections.abc import Callable
from pathlib import Path
from typing import cast

import dspy

from jobcrawler import config
from jobcrawler.generate.budget import CappedLM
from jobcrawler.generate.evaluate import evaluate_generator
from jobcrawler.generate.generator import (
    CrawlerGenerator,
    load_generator,
    save_generator,
)
from jobcrawler.generate.optimize import (
    bootstrap_compile,
    gepa_compile,
    run_optimization,
    select_optimizer,
)
from jobcrawler.generate.scoring import (
    CandidateResult,
    make_bootstrap_metric,
    make_metric,
)
from jobcrawler.generate.snapshot import prefetch_corpus
from jobcrawler.generate.tools import corpus_host_evidence
from jobcrawler.runtime.contract import declared_extra_hosts
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.sandbox import validate_crawler
from jobcrawler.state import StateStore
from jobcrawler.trainset import load_records, optimization_pool, split_by_site
from jobcrawler.urls import normalize_host

_TARGET_ITEMS = 30  # the positions-axis saturation target (mirrors cli_generate)
_MIN_SITES = 3  # need at least one site each for train / val / held-out


def optimize_command(args: argparse.Namespace) -> int:
    """Single-flight wrapper: hold the §4.11 optimize lock for the whole run.

    The offline compile is multi-hour; two concurrent runs would fight over the
    trainset and generator dir. Acquiring the lock here makes both the manual
    ``jobcrawl optimize`` and the scheduler's out-of-band dispatch participate in
    one single-flight — a second invocation exits cleanly instead of piling on.
    """
    from jobcrawler.optimize_job import AlreadyRunning, optimize_lock

    try:
        with optimize_lock():
            return _run_optimize(args)
    except AlreadyRunning as exc:
        print(f"optimize: {exc}; exiting")
        return 0


def _run_optimize(args: argparse.Namespace) -> int:
    from jobcrawler.cli import _db_path  # avoid an import cycle

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

    # Guard on distinct *sites*, not examples: split_by_site partitions by site, so a
    # disjoint train/val/held-out needs >= holdout+val+1 sites (a many-example,
    # few-site pool would otherwise leave train empty).
    n_sites = len({str(ex.site_url) for ex in examples})
    if n_sites < _MIN_SITES:
        print(
            f"no admitted trainset yet: {n_sites} site(s) cleared "
            f"{config.trainset_stable_runs()} stable run(s) — need >= {_MIN_SITES} "
            "for a disjoint train/val/held-out split (§4.11)"
        )
        return 0

    holdout_n, val_n = _split_sizes(n_sites)
    train, val, holdout = split_by_site(examples, holdout=holdout_n, val=val_n)
    result = _optimize(train, val, holdout)

    for note_key in ("pareto_health", "budget_health"):
        if result.get(note_key):
            print(f"warning: {result[note_key]}")

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
        if getattr(args, "adopt", False):
            # "Full auto, gated by proof": the margin gate is the proof, --adopt flips
            # the live pointer so a running daemon picks it up — no env change/restart.
            from datetime import datetime, timezone

            from jobcrawler import generator_pointer
            from jobcrawler.notify import notify

            generator_pointer.set_active(out)
            # Put it under §4.11 probation: the daemon's regression watcher rolls it
            # back automatically if post-adoption self-heals start failing more (P4c).
            generator_pointer.record_adoption(out, at=datetime.now(timezone.utc))
            notify(
                "generator.adopted",
                f"adopted optimized generator (held-out margin cleared): {out}",
                path=str(out),
                margin=round(_as_float(result.get("margin")), 1),
            )
            print(f"  adopted: active generator -> {out}")
            print("  rollback: jobcrawl rollback-generator")
        else:
            print(f"  deploy: set JOBCRAWLER_GENERATOR_PATH={out}")
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
    root_lm = dspy.LM(config.require_model(), max_tokens=max_tokens)
    # Required cheap sub_lm + the §4.2 host sub-call kill (never the silent root
    # fallback, which bills every llm_query at planner rates).
    sub_lm = CappedLM(
        config.require_sub_model(),
        max_subcalls=config.max_subcalls(),
        max_tokens=max_tokens,
    )

    def core(source: str, carrier: object) -> CandidateResult:
        return validate_crawler(source, allowed_hosts=_candidate_hosts(source, carrier))

    bootstrap_metric = make_bootstrap_metric(
        core,
        target_n=_TARGET_ITEMS,
        min_description_chars=config.min_description_chars(),
    )
    eval_metric = make_metric(
        core,
        target_n=_TARGET_ITEMS,
        min_items=config.min_items(),
        min_description_chars=config.min_description_chars(),
    )
    # The incumbent: the pinned generator.json if any, else baseline (§4.11 boot-load).
    baseline = load_generator(
        sub_lm=sub_lm,
        max_iterations=config.max_iterations(),
        max_llm_calls=config.max_llm_calls(),
    )

    optimized_factory = _build_factory(
        select_optimizer(len(train)),
        baseline=baseline,
        train=train,
        val=val,
        bootstrap_metric=bootstrap_metric,
        eval_metric=eval_metric,
        max_tokens=max_tokens,
    )
    # Average several held-out passes (the generator is stochastic) so a marginal win
    # isn't promoted on sampling luck — promotion rigor over a single noisy pass (§4.11).
    passes = config.promotion_eval_passes()

    def eval_fn(program: object, *, devset: object, metric: object) -> float:
        return evaluate_generator(
            cast("CrawlerGenerator", program),
            devset=cast("list[dspy.Example]", devset),
            metric=cast("Callable[..., object]", metric),
            passes=passes,
        )

    with dspy.context(lm=root_lm):
        # The minibatch the saturation guard reads: the incumbent's per-site scores.
        minibatch_scores = [
            bootstrap_metric(example, baseline(**example.inputs())) for example in train
        ]
        return run_optimization(
            baseline=baseline,
            optimized_factory=optimized_factory,
            trainset=train,
            valset=val,
            holdout=holdout,
            metric=eval_metric,
            minibatch_scores=minibatch_scores,
            eval_fn=eval_fn,
        )


def _build_factory(
    optimizer: str,
    *,
    baseline: CrawlerGenerator,
    train: list[dspy.Example],
    val: list[dspy.Example],
    bootstrap_metric: Callable[..., float],
    eval_metric: Callable[..., object],
    max_tokens: int,
) -> Callable[[], object]:
    """Pick the optimizer rung's compile step (§4.11 ladder).

    ``select_optimizer`` keys on the *example* count: ``bootstrap_fewshot`` is the
    cheap always-valid rung; ``gepa`` evolves the strategy instruction once the
    trainset is feedback-rich. ``mipro`` (documented-not-built in v1) falls *forward*
    to GEPA — the strongest built rung — with a loud notice, never a silent drop to
    bootstrap. ``none`` never reaches here (``run_optimization`` short-circuits it),
    but maps to bootstrap as a harmless never-called placeholder.
    """
    if optimizer == "mipro":
        print(
            f"note: {len(train)} examples reach the MIPRO tier (§4.11), but MIPRO is "
            "documented-not-built in v1 — using the GEPA rung instead"
        )
    if optimizer in ("gepa", "mipro"):
        # Fail fast on a missing reflection model *before* the expensive minibatch
        # scoring, then build the LM lazily inside the factory (gepa-only).
        reflection_model = config.require_reflection_model()

        def gepa_factory() -> object:
            reflection_lm = dspy.LM(reflection_model, max_tokens=max_tokens)
            return gepa_compile(
                baseline,
                trainset=train,
                valset=val,
                reflection_lm=reflection_lm,
                metric=eval_metric,
            )

        return gepa_factory

    def bootstrap_factory() -> object:
        return bootstrap_compile(
            baseline, trainset=train, valset=val, metric=bootstrap_metric
        )

    return bootstrap_factory


def _split_sizes(n_sites: int) -> tuple[int, int]:
    """Scale the ``(holdout, val)`` site counts with the pool — ~1/5 each, >= 1, and
    always leaving train non-empty (§4.11).

    At the 3-site floor this is ``(1, 1)`` (train = 1 site), matching the original
    fixed split; it grows with the pool so the held-out gains the statistical power
    to clear the noise-floor margin (``promotion_margin`` is ``100/|held-out|`` — a
    bigger held-out means a smaller, more discriminating margin, so real gains aren't
    drowned by one flipped example).
    """
    holdout = max(1, n_sites // 5)
    val = max(1, n_sites // 5)
    while holdout + val >= n_sites and (holdout > 1 or val > 1):
        if val > 1:
            val -= 1
        else:
            holdout -= 1
    return holdout, val


def _prefetch_corpus(site_url: str) -> dict[str, object]:
    """Re-prefetch a hard case's corpus fresh, so the optimizer scores the strategy
    against the site's *current* (drifted) state (§4.11)."""
    host = normalize_host(site_url)
    return prefetch_corpus(site_url, Ctx({host} if host else set()))


def _stable_runs(store: StateStore, slug: str) -> int:
    """Consecutive most-recent *finished* runs that found positions (non-stale, §4.8)."""
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


def _carrier_corpus(carrier: object) -> object:
    if isinstance(carrier, dspy.Example):
        return carrier.get("corpus", None)
    return getattr(carrier, "corpus", None)


def _candidate_hosts(source: str, carrier: object) -> set[str]:
    """The metric's §4.4 validation allowlist for one optimizer candidate (§8).

    Registry host + the candidate's declared EXTRA_HOSTS *bounded by the example's
    corpus evidence* — the same inference live admission (gate-1(b)) applies,
    recomputed here from the carrier's recorded corpus so an optimizer candidate
    can't authorize an arbitrary host merely by declaring it. No robots probe in
    this offline metric (the live gate owns that at delivery time); an unparseable
    candidate falls back to the registry host alone and scores accordingly.
    """
    site_url = _carrier_site_url(carrier)
    host = normalize_host(site_url)
    base = {host} if host else set()
    try:
        declared = {
            h
            for h in (
                normalize_host(f"https://{e}") for e in declared_extra_hosts(source)
            )
            if h
        }
    except SyntaxError:
        return base
    if not declared:
        return base
    evidence = set(corpus_host_evidence(_carrier_corpus(carrier), site_url))
    return base | (declared & evidence)


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
