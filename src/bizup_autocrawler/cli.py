"""Operator CLI (§10): ``autocrawl run | generate | validate | crawl | list``.

``run`` is the primary deployment entrypoint (the service schedules itself, §4.9);
``crawl`` is for manual one-shot runs and CI; ``validate`` dry-runs a committed
script through the §4.4 sandbox with **no ingest**; ``generate`` runs the RLM and
gates before writing ``crawlers/<slug>.py`` (it needs a configured LM + Deno, so
its heavy deps are imported lazily); ``list`` shows sites, script presence, and
last-run health. The list/validate/crawl wiring is thin over already-tested
backends; the live paths are correct-by-construction.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib
import logging
import signal
from collections.abc import Sequence
from pathlib import Path

from bizup_autocrawler import config
from bizup_autocrawler.generate.scoring import CandidateResult
from bizup_autocrawler.health import HealthState, SelfHealer, staleness_candidate
from bizup_autocrawler.ingest import NewsClient
from bizup_autocrawler.runtime.contract import load_crawler
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.runner import CrawlSummary, crawl_site, probe_crawler
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Scheduler, Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.state import RunRecord, StateStore
from bizup_autocrawler.trainset import record_failure
from bizup_autocrawler.urls import normalize_host

_CRAWLERS_DIR = Path(__file__).parent / "crawlers"

_log = logging.getLogger("bizup_autocrawler.cli")


def _crawler_file(slug: str, crawlers_dir: Path = _CRAWLERS_DIR) -> Path:
    return crawlers_dir / f"{slug}.py"


def _site_url_of(source: str) -> str:
    """Read the module-level SITE constant from crawler source without importing."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SITE" for t in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return ""


def _db_path() -> str:
    url = config.state_url()
    return url.removeprefix("sqlite:///") if url.startswith("sqlite:") else url


# -- list --------------------------------------------------------------------


def _health_label(
    store: StateStore, slug: str, history: Sequence[RunRecord], has_run: bool
) -> str:
    """The health column: persisted generation-failed wins over a re-derived stale.

    A generation-failed site still *looks* like a staleness candidate from its run
    history, so re-deriving would mislabel it "stale" (implying it self-heals); the
    persisted state (§4.8) is authoritative for that terminal case.
    """
    if store.get_health(slug) == HealthState.GENERATION_FAILED.value:
        return "gen-failed"
    if staleness_candidate(list(history)):
        return "stale"
    return "ok" if has_run else "new"


def render_list(
    sites: Sequence[Site], store: StateStore, *, crawlers_dir: Path = _CRAWLERS_DIR
) -> list[str]:
    """One line per site: enabled flag, committed-script presence, last item count."""
    rows: list[str] = []
    for site in sites:
        has_script = _crawler_file(site.slug, crawlers_dir).exists()
        history = store.runs_for(site.slug)
        last = history[0] if history else None
        items = str(last.items_found) if last is not None else "-"
        health = _health_label(store, site.slug, history, last is not None)
        rows.append(
            f"{site.slug:24} {'on' if site.enabled else 'off':3} "
            f"{'script' if has_script else 'no-script':9} "
            f"last_items={items:>4} health={health}"
        )
    return rows


# -- validate ----------------------------------------------------------------


def validate_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> CandidateResult:
    """Dry-run a committed script through the sandbox — no ingest (§10)."""
    source = _crawler_file(slug, crawlers_dir).read_text()
    host = normalize_host(_site_url_of(source))
    return validate_crawler(source, allowed_hosts={host} if host else set())


# -- crawl -------------------------------------------------------------------


def crawl_one(site: Site, *, store: StateStore, news: NewsClient) -> CrawlSummary:
    """Import the committed crawler and run it through the full ingest path."""
    module = importlib.import_module(f"bizup_autocrawler.crawlers.{site.slug}")
    site_url, crawl_fn = load_crawler(module)
    ctx = Ctx({normalize_host(site_url)})
    return crawl_site(
        site.slug,
        crawl_fn,
        ctx,
        store,
        news,
        first_crawl_cap=config.first_crawl_cap(),
        min_body_chars=config.min_body_chars(),  # only ingest complete (§4.6)
        paywall_markers=config.paywall_markers(),  # drop paywall teasers (§4.6)
        crawler_name=site.name,  # attribute each item to its source crawler
        crawler_url=site.url,
    )


# -- command handlers --------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    try:
        for line in render_list(load_sites(config.sites_path()), store):
            print(line)
    finally:
        store.close()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_one(args.slug)
    if result.exception is not None:
        print(f"FAIL {args.slug}: {result.exception.type}: {result.exception.message}")
        return 1
    print(
        f"OK {args.slug}: {result.item_count} items, "
        f"{len(result.schema_errors)} schema errors (no ingest)"
    )
    return 0 if result.item_count > 0 else 1


def _crawl_targets(args: argparse.Namespace) -> list[Site]:
    sites = load_sites(config.sites_path())
    if args.all:
        return [s for s in sites if s.enabled]
    site = find_site(sites, args.slug)
    return [site] if site is not None else []


def cmd_crawl(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    news = NewsClient(timeout=config.ingest_timeout())
    try:
        for site in _crawl_targets(args):
            summary = crawl_one(site, store=store, news=news)
            print(
                f"{summary.site}: found={summary.items_found} "
                f"new={summary.new_items} incomplete={summary.incomplete} "
                f"errors={summary.errors}"
            )
    finally:
        store.close()
    return 0


def _probe(slug: str) -> int:
    """Re-run a committed crawler and count its complete items, no ingest (§4.8).

    The re-probe the self-healer uses to tell a real failure from a transient
    outage. A site whose committed script is missing or unimportable probes to 0
    (a confirmed failure → regenerate), never an exception that aborts the loop.
    """
    site = find_site(load_sites(config.sites_path()), slug)
    if site is None:
        return 0
    try:
        module = importlib.import_module(f"bizup_autocrawler.crawlers.{site.slug}")
        site_url, crawl_fn = load_crawler(module)
    except Exception:
        return 0
    return probe_crawler(
        crawl_fn,
        Ctx({normalize_host(site_url)}),
        min_body_chars=config.min_body_chars(),
        paywall_markers=config.paywall_markers(),
    )


def _record_site_failure(sites: Sequence[Site], slug: str) -> None:
    """Capture a confirmed-failed site as a §4.11 hard case (the optimizer's signal).

    Only the site identity / render / hints are stored; the corpus is re-prefetched
    fresh at optimize time so the strategy is scored against the site's *current*
    (drifted) state (:func:`bizup_autocrawler.trainset.record_failure`).
    """
    site = find_site(sites, slug)
    if site is not None:
        record_failure(
            config.trainset_path(),
            slug=slug,
            site_url=site.url,
            render=site.render,
            hints=site.hints,
        )


def _build_healer(store: StateStore, sites: Sequence[Site]) -> SelfHealer:
    """The production §4.8 self-healer the scheduler drives on its regen timer.

    ``reprobe`` re-runs the live crawler (a real failure vs a transient blip);
    ``regenerate`` re-runs the generator for the slug (lazy import — it pulls dspy).
    A confirmed failure is first captured as a §4.11 hard case, so a genuinely
    broken site feeds the offline optimizer before self-healing tries to fix it.
    Both live calls run off the event loop via ``to_thread``.
    """

    async def reprobe(slug: str) -> int:
        return await asyncio.to_thread(_probe, slug)

    async def regenerate(slug: str) -> bool:
        from bizup_autocrawler.cli_generate import regenerate_one

        return await asyncio.to_thread(regenerate_one, slug)

    return SelfHealer(
        store,
        reprobe=reprobe,
        regenerate=regenerate,
        on_confirmed_failure=lambda slug: _record_site_failure(sites, slug),
    )


def cmd_run(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    news = NewsClient(timeout=config.ingest_timeout())
    sites = load_sites(config.sites_path())

    # A prior daemon killed mid-crawl leaves finished=NULL run rows; the scheduler
    # would treat those sites as perpetually in-flight and never re-dispatch them.
    # Reap before the scheduler starts (single-process §4.9: nothing is in-flight yet).
    reaped = store.reap_orphans()
    if reaped:
        _log.warning("reaped %d orphaned run row(s) from a prior crash", reaped)

    async def crawl(site: Site) -> CrawlSummary:
        return await asyncio.to_thread(crawl_one, site, store=store, news=news)

    scheduler = Scheduler(
        sites, store=store, crawl=crawl, healer=_build_healer(store, sites)
    )

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)  # graceful drain (§4.9)
            except NotImplementedError:
                pass  # signal handlers unavailable (e.g. non-Unix); Ctrl-C still works
        await scheduler.run(stop=stop)

    try:
        asyncio.run(serve())
    finally:
        store.close()
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    # The generator needs a configured LM + Deno sandbox + live fetch; its deps
    # are heavy, so import lazily and let the runtime surface any missing setup.
    from bizup_autocrawler.cli_generate import generate_command

    return generate_command(args)


def cmd_optimize(args: argparse.Namespace) -> int:
    # The offline §4.11 self-improvement job (build trainset → optimize → promote);
    # its deps (dspy + the sandbox) are heavy, so import lazily.
    from bizup_autocrawler.cli_optimize import optimize_command

    return optimize_command(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autocrawl", description="RLM autocrawler")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the scheduler daemon").set_defaults(func=cmd_run)

    gen = sub.add_parser("generate", help="run RLM, gate, write crawlers/<slug>.py")
    target = gen.add_mutually_exclusive_group(required=True)
    target.add_argument("slug", nargs="?")
    target.add_argument("--all", action="store_true")
    target.add_argument("--stale", action="store_true")
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate", help="dry-run a script, no ingest")
    val.add_argument("slug")
    val.set_defaults(func=cmd_validate)

    crawl = sub.add_parser("crawl", help="run committed scripts, dedup, ingest")
    crawl_target = crawl.add_mutually_exclusive_group(required=True)
    crawl_target.add_argument("slug", nargs="?")
    crawl_target.add_argument("--all", action="store_true")
    crawl.set_defaults(func=cmd_crawl)

    sub.add_parser("list", help="show sites, script presence, health").set_defaults(
        func=cmd_list
    )

    sub.add_parser(
        "optimize",
        help="offline §4.11 self-improvement: build trainset, optimize, promote",
    ).set_defaults(func=cmd_optimize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
