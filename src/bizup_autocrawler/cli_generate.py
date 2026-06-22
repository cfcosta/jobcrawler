"""The ``autocrawl generate`` orchestration (§4.2, §6) — needs a live LM + Deno.

Kept separate from ``cli.py`` so importing the CLI never pulls dspy. The full §6
machinery runs here:

  1. prefetch the corpus through ctx;
  2. build the §4.2 host tools (fetch / find_feeds / get_sitemap / validate_crawler)
     and an explicit ``sub_lm`` (never the silent root fallback) and hand them to
     the RLM;
  3. generate N candidates (distinct rollout_id @ temp 1.0) and argmax over the
     **graded scalar** via ``best_of_n`` + ``make_reward_fn`` (the core runs the
     candidate through the §4.4 sandbox);
  4. gate the winner with the full set — AST → must-observe (validate-before-SUBMIT
     / consult-feeds-before-scrape, read off the trajectory) → runtime completeness
     → two-run Jaccard stability — and, only on a pass, *deliver* the crawler: an
     operator-run ``generate`` writes ``crawlers/<slug>.py`` directly, but the
     unattended §4.8 self-heal (``regenerate_one``) honors ``AUTOCRAWLER_REVIEW_GATE``
     — on by default — and instead *stages* it to ``crawlers/_pending/<slug>.py`` for
     a human to ``promote`` (§14.2), never silently replacing the live crawler.

The whole path is live (LM, Deno, network); the gating + delivery logic is unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

import dspy

from bizup_autocrawler import config
from bizup_autocrawler.generate.gates import (
    StabilityVerdict,
    check_ast,
    check_completeness,
    must_observe_failure,
    stability_verdict,
)
from bizup_autocrawler.generate.adversarial import cross_validate
from bizup_autocrawler.generate.budget import CappedLM
from bizup_autocrawler.generate.generator import best_of_n, load_generator
from bizup_autocrawler.generate.scoring import CandidateResult, make_reward_fn
from bizup_autocrawler.generate.snapshot import prefetch_corpus
from bizup_autocrawler.generate.tools import make_host_tools
from bizup_autocrawler.health import RegenOutcome
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.trainset import append_record, make_example
from bizup_autocrawler.urls import normalize_host

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_CRAWLERS_DIR = Path(__file__).parent / "crawlers"
_TARGET_ITEMS = 30  # items-axis saturation for the graded scalar


def generate_command(args: argparse.Namespace) -> int:
    targets = _select(load_sites(config.sites_path()), args)
    if not targets:
        print("no matching sites to generate")
        return 1
    return asyncio.run(_run(targets))


def regenerate_one(slug: str) -> RegenOutcome:
    """Regenerate one site's crawler, reporting how it was delivered (§4.8, §14.2).

    The self-healing entry point — the scheduler's regen timer calls this for a
    confirmed-stale site. Runs the full §6 generation for that one site, then honors
    ``AUTOCRAWLER_REVIEW_GATE`` (on by default): a gate-passing crawler is *staged* to
    crawlers/_pending/<slug>.py for human promotion rather than overwriting the live
    crawler the daemon is running. With the gate off it goes live (auto-heal). A
    missing site or an exhausted generation reports ``FAILED``.
    """
    site = find_site(load_sites(config.sites_path()), slug)
    if site is None:
        return RegenOutcome.FAILED
    root_lm, sub_lm = _build_lms()
    return asyncio.run(_generate_one(site, root_lm, sub_lm, stage=config.review_gate()))


def _build_lms() -> tuple[dspy.LM, dspy.LM]:
    """The §4.2 LM pair: a strong root planner + the cheap, host-capped ``sub_lm``.

    ``require_sub_model()`` errors when ``AUTOCRAWLER_SUB_MODEL`` is unset rather than
    silently billing every in-sandbox ``llm_query`` at the root planner's rate, and
    ``CappedLM`` adds the unconditional sub-call kill (the only hard stop on a
    catch-and-continue loop). Both caps are host-side, never seen by the generator.
    """
    max_tokens = config.max_tokens()
    root_lm = dspy.LM(config.require_model(), max_tokens=max_tokens)
    sub_lm = CappedLM(
        config.require_sub_model(),
        max_subcalls=config.max_subcalls(),
        max_tokens=max_tokens,
    )
    return root_lm, sub_lm


def _select(sites: list[Site], args: argparse.Namespace) -> list[Site]:
    if args.all:
        return [s for s in sites if s.enabled]
    if args.stale:
        print("--stale is driven by `autocrawl run`'s health loop (§4.8)")
        return []
    site = find_site(sites, args.slug)
    return [site] if site is not None else []


async def _run(targets: list[Site]) -> int:
    root_lm, sub_lm = _build_lms()
    failures = 0
    for site in targets:
        # Operator-run generate writes live (stage defaults off); only a generation
        # that produced no gate-passing crawler counts as a failure.
        if await _generate_one(site, root_lm, sub_lm) is RegenOutcome.FAILED:
            failures += 1
    return 1 if failures else 0


async def _generate_one(
    site: Site, root_lm: dspy.LM, sub_lm: dspy.LM, *, stage: bool = False
) -> RegenOutcome:
    hosts = {normalize_host(site.url)}
    ctx = Ctx(hosts)
    corpus = prefetch_corpus(site.url, ctx)
    # Boot-load the §4.11-optimized strategy if one is pinned, else baseline; the
    # live tools/sub_lm/caps are rebuilt fresh and only the strategy state reloads.
    generator = load_generator(
        tools=make_host_tools(ctx, hosts),
        sub_lm=sub_lm,
        max_iterations=config.max_iterations(),
        max_llm_calls=config.max_llm_calls(),
    )

    inputs: dict[str, object] = {
        "site_url": site.url,
        "corpus": corpus,
        "render": site.render,
        "hints": site.hints,
    }

    def core(source: str, carrier: object) -> CandidateResult:
        return validate_crawler(source, allowed_hosts=hosts)

    reward_fn = make_reward_fn(
        core, target_n=_TARGET_ITEMS, min_body_chars=config.min_body_chars()
    )

    async def make_attempt(i: int) -> object:
        # track_usage so each candidate's $ cost is readable for the §4.2 fan-out cap.
        with dspy.context(
            lm=root_lm.copy(rollout_id=i, temperature=1.0), track_usage=True
        ):
            return await generator.aforward(
                site_url=site.url,
                corpus=corpus,
                render=site.render,
                hints=site.hints,
            )

    best, score = await best_of_n(
        make_attempt,
        reward=lambda pred: reward_fn(inputs, pred),
        n=config.fanout_n(),
        threshold=1.0,
        cost=_pred_cost,  # aggregate FANOUT_SPEND_CAP across the fan-out (§4.2)
        spend_cap=config.fanout_spend_cap_usd(),
    )
    if best is None:
        print(f"FAIL {site.slug}: no candidate produced")
        return RegenOutcome.FAILED

    source = _source_text(best)
    # The enumeration *count* floor is NOT applied here: the 10s validation sandbox
    # fetches only a handful of pages, far fewer than a site's full recent set, so a
    # count gate would reject every candidate (and can't tell feed-only from a
    # sitemap crawler — both fetch ~the same few in the window). The generator is
    # instead steered toward full enumeration by site_features (it sees
    # expected_recent) and the must-observe "consult the sitemap" gate; the
    # count-based floor belongs at runtime, over a full crawl.
    ok, reason = gate_winner(best, source, hosts)
    if not ok:
        print(f"FAIL {site.slug}: {reason}")
        return RegenOutcome.FAILED
    outcome = _deliver(site.slug, source, stage=stage)
    if outcome is RegenOutcome.STAGED:
        print(
            f"OK {site.slug}: staged crawlers/_pending/{site.slug}.py (score "
            f"{score:.2f}) — run `autocrawl promote {site.slug}` to go live (§14.2)"
        )
    else:
        print(f"OK {site.slug}: wrote crawlers/{site.slug}.py (score {score:.2f})")
    _record_for_trainset(site, ctx, corpus, best, source, hosts)
    return outcome


def _deliver(
    slug: str, source: str, *, stage: bool, crawlers_dir: Path = _CRAWLERS_DIR
) -> RegenOutcome:
    """Write a gate-passing crawler to its destination and report the outcome (§14.2).

    With ``stage`` (the self-heal default — ``AUTOCRAWLER_REVIEW_GATE`` on) the crawler
    lands in ``crawlers/_pending/<slug>.py`` for a human to ``promote``; the live
    crawler the daemon runs is deliberately left untouched. Otherwise it goes live.
    """
    if stage:
        pending = crawlers_dir / "_pending"
        pending.mkdir(parents=True, exist_ok=True)
        (pending / f"{slug}.py").write_text(source)
        return RegenOutcome.STAGED
    (crawlers_dir / f"{slug}.py").write_text(source)
    return RegenOutcome.PROMOTED


def promote_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> tuple[bool, str]:
    """Promote a staged crawler to live (the human half of the §14.2 review gate).

    Moves ``crawlers/_pending/<slug>.py`` over ``crawlers/<slug>.py`` and consumes the
    staged copy. Returns ``(ok, message)``; ``ok`` is False (and nothing is written) if
    no crawler is staged for the slug.
    """
    pending = crawlers_dir / "_pending" / f"{slug}.py"
    if not pending.exists():
        return False, f"no staged crawler for {slug!r} (nothing in crawlers/_pending/)"
    (crawlers_dir / f"{slug}.py").write_text(pending.read_text())
    pending.unlink()
    return True, f"promoted {slug}: crawlers/_pending/{slug}.py → crawlers/{slug}.py"


def gate_winner(
    pred: object, source: str, hosts: set[str], *, expected_items: int | None = None
) -> tuple[bool, str]:
    """The full §6 gate set on the selected winner; returns (passed, reason).

    ``expected_items`` (the site's recent-article count, from its news sitemap)
    activates the enumeration floor — a feed-sized sample of a far larger site is
    rejected as under-enumeration (§6).
    """
    ast_result = check_ast(source)
    if not ast_result.passed:
        return False, f"AST gate: {ast_result.reason}"

    must_observe = must_observe_failure(
        validated=_called(pred, "validate_crawler"),
        discovery_method=_discovery(pred),
        consulted_discovery=_called(pred, "find_feeds") or _called(pred, "get_sitemap"),
    )
    if must_observe is not None:
        return False, f"must-observe: {must_observe}"

    first = validate_crawler(source, allowed_hosts=hosts)
    completeness = check_completeness(
        first,
        min_items=config.min_items(),
        min_body_chars=config.min_body_chars(),
        allowed_hosts=hosts,
        expected_items=expected_items,
        enum_floor_ratio=config.enum_floor_ratio(),
        min_body_quality=config.min_body_quality(),
    )
    if not completeness.passed:
        return False, f"completeness: {completeness.reason}"

    second = validate_crawler(source, allowed_hosts=hosts)
    verdict, jaccard = stability_verdict(
        _sample_urls(first), _sample_urls(second), threshold=config.stability_jaccard()
    )
    if verdict is StabilityVerdict.REJECT:
        return False, f"stability: near-random across two runs (jaccard {jaccard:.2f})"

    return True, "ok"


def _pred_cost(pred: object) -> float:
    """Best-effort USD cost of one candidate from dspy's LM-usage tracking.

    Returns 0.0 when no usage/cost signal is available (e.g. a provider litellm has
    no cost map for) — the §4.2 fan-out is then bounded structurally by ``fanout_n``
    and each candidate by the per-trajectory ``MAX_SUBCALLS`` kill, never by a
    fabricated number.
    """
    getter = getattr(pred, "get_lm_usage", None)
    if not callable(getter):
        return 0.0
    try:
        usage = getter() or {}
    except Exception:
        return 0.0
    if not isinstance(usage, dict):
        return 0.0
    total = 0.0
    for entry in usage.values():
        if isinstance(entry, dict) and isinstance(entry.get("cost"), (int, float)):
            total += float(entry["cost"])
    return total


def _sample_urls(result: CandidateResult) -> list[str]:
    return [str(sample.get("url", "")) for sample in result.samples]


def _called(pred: object, tool: str) -> bool:
    return tool in str(getattr(pred, "trajectory", ""))


def _discovery(pred: object) -> str:
    return str(getattr(pred, "discovery_method", "scrape"))


def _source_text(pred: object) -> str:
    src = getattr(pred, "crawler_source", "")
    code = getattr(src, "code", None)
    return code if isinstance(code, str) else str(src)


def _record_for_trainset(
    site: Site,
    ctx: Ctx,
    corpus: dict[str, object],
    pred: object,
    source: str,
    hosts: set[str],
) -> None:
    """Append an admitted example to the §4.11 trainset — only if the §8 adversarial
    cross-validation over the *actual* items also passes (the §6 gates already did).
    The per-site stability admission is applied later, at optimize time.
    """
    result = validate_crawler(source, allowed_hosts=hosts)
    verdict = cross_validate(
        result.samples,
        live_titles=_live_titles(ctx, result.samples),
        allowed_hosts=hosts,
    )
    if not verdict.passed:
        print(f"  not recorded for trainset: adversarial failed ({verdict.reason})")
        return
    append_record(
        config.trainset_path(),
        slug=site.slug,
        example=make_example(
            site_url=site.url,
            corpus=corpus,
            render=site.render,
            hints=site.hints,
            discovery_method=_discovery(pred),
            discovery_url=str(getattr(pred, "discovery_url", "") or ""),
            crawler_source=source,
        ),
    )
    print(f"  recorded for §4.11 trainset -> {config.trainset_path()}")


def _live_titles(ctx: Ctx, samples: list[dict]) -> dict[str, str]:
    """Best-effort live ``<title>`` per sample url, for the §8 title-match check."""
    titles: dict[str, str] = {}
    for sample in samples:
        url = str(sample.get("url") or "")
        if not url:
            continue
        try:
            match = _TITLE_RE.search(ctx.fetch(url))
        except Exception:
            continue
        if match:
            titles[url] = re.sub(r"\s+", " ", match.group(1)).strip()
    return titles
