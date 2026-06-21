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
     → two-run Jaccard stability — and write ``crawlers/<slug>.py`` for review (§8)
     only on a pass.

The whole path is live (LM, Deno, network); the gating logic is unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
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
from bizup_autocrawler.generate.generator import best_of_n, load_generator
from bizup_autocrawler.generate.scoring import CandidateResult, make_reward_fn
from bizup_autocrawler.generate.snapshot import prefetch_corpus
from bizup_autocrawler.generate.tools import make_host_tools
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.urls import normalize_host

_CRAWLERS_DIR = Path(__file__).parent / "crawlers"
_TARGET_ITEMS = 30  # items-axis saturation for the graded scalar


def generate_command(args: argparse.Namespace) -> int:
    targets = _select(load_sites(config.sites_path()), args)
    if not targets:
        print("no matching sites to generate")
        return 1
    return asyncio.run(_run(targets))


def _select(sites: list[Site], args: argparse.Namespace) -> list[Site]:
    if args.all:
        return [s for s in sites if s.enabled]
    if args.stale:
        print("--stale is driven by `autocrawl run`'s health loop (§4.8)")
        return []
    site = find_site(sites, args.slug)
    return [site] if site is not None else []


async def _run(targets: list[Site]) -> int:
    max_tokens = config.max_tokens()
    root_lm = dspy.LM(config.model(), max_tokens=max_tokens)
    # Explicit sub_lm — never None, which dspy silently bills at root rates (§4.2).
    sub_lm = dspy.LM(config.sub_model() or config.model(), max_tokens=max_tokens)
    failures = 0
    for site in targets:
        if not await _generate_one(site, root_lm, sub_lm):
            failures += 1
    return 1 if failures else 0


async def _generate_one(site: Site, root_lm: dspy.LM, sub_lm: dspy.LM) -> bool:
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
        with dspy.context(lm=root_lm.copy(rollout_id=i, temperature=1.0)):
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
    )
    if best is None:
        print(f"FAIL {site.slug}: no candidate produced")
        return False

    source = _source_text(best)
    ok, reason = gate_winner(best, source, hosts)
    if not ok:
        print(f"FAIL {site.slug}: {reason}")
        return False
    (_CRAWLERS_DIR / f"{site.slug}.py").write_text(source)
    print(
        f"OK {site.slug}: wrote crawlers/{site.slug}.py (score {score:.2f}) for review"
    )
    return True


def gate_winner(pred: object, source: str, hosts: set[str]) -> tuple[bool, str]:
    """The full §6 gate set on the selected winner; returns (passed, reason)."""
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
