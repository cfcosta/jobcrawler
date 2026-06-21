"""The ``autocrawl generate`` orchestration (§4.2, §6) — needs a live LM + Deno.

Kept separate from ``cli.py`` so importing the CLI never pulls dspy. Builds the
corpus by fetching the site, runs the RLM generator under the configured root LM,
gates the candidate (§6 AST + runtime completeness through the sandbox), and
writes ``crawlers/<slug>.py`` for human review (§8) only on a pass. The whole
path is live (LM, Deno, network), so it is wired correct-by-construction rather
than unit-tested.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import dspy

from bizup_autocrawler import config
from bizup_autocrawler.generate.gates import check_ast, check_completeness
from bizup_autocrawler.generate.generator import CrawlerGenerator
from bizup_autocrawler.generate.snapshot import prefetch_corpus
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.urls import normalize_host

_CRAWLERS_DIR = Path(__file__).parent / "crawlers"


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
        # --stale regeneration is driven by the §4.8 health loop, which owns the store.
        print("--stale is driven by `autocrawl run`'s health loop (§4.8)")
        return []
    site = find_site(sites, args.slug)
    return [site] if site is not None else []


async def _run(targets: list[Site]) -> int:
    lm = dspy.LM(config.model())
    generator = CrawlerGenerator()
    failures = 0
    for site in targets:
        if not await _generate_one(site, generator, lm):
            failures += 1
    return 1 if failures else 0


async def _generate_one(site: Site, generator: CrawlerGenerator, lm: dspy.LM) -> bool:
    host = normalize_host(site.url)
    corpus = prefetch_corpus(site.url, Ctx({host}))
    with dspy.context(lm=lm):
        pred = await generator.aforward(
            site_url=site.url, corpus=corpus, render=site.render, hints=site.hints
        )
    source = getattr(pred.crawler_source, "code", None) or str(pred.crawler_source)
    if not _gates_pass(source, host):
        print(f"FAIL {site.slug}: candidate did not pass the §6 gates")
        return False
    (_CRAWLERS_DIR / f"{site.slug}.py").write_text(source)
    print(f"OK {site.slug}: wrote crawlers/{site.slug}.py for review")
    return True


def _gates_pass(source: str, host: str) -> bool:
    if not check_ast(source).passed:
        return False
    result = validate_crawler(source, allowed_hosts={host})
    return check_completeness(
        result,
        min_items=config.min_items(),
        min_body_chars=config.min_body_chars(),
        allowed_hosts={host},
    ).passed
