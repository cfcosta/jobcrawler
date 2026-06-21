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
from collections.abc import Sequence
from pathlib import Path

from bizup_autocrawler import config
from bizup_autocrawler.generate.scoring import CandidateResult
from bizup_autocrawler.ingest import NewsClient
from bizup_autocrawler.runtime.contract import load_crawler
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.runner import CrawlSummary, crawl_site
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Scheduler, Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.state import StateStore
from bizup_autocrawler.urls import normalize_host

_CRAWLERS_DIR = Path(__file__).parent / "crawlers"


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


def render_list(
    sites: Sequence[Site], store: StateStore, *, crawlers_dir: Path = _CRAWLERS_DIR
) -> list[str]:
    """One line per site: enabled flag, committed-script presence, last item count."""
    rows: list[str] = []
    for site in sites:
        has_script = _crawler_file(site.slug, crawlers_dir).exists()
        last = store.last_run(site.slug)
        items = str(last.items_found) if last is not None else "-"
        rows.append(
            f"{site.slug:24} {'on' if site.enabled else 'off':3} "
            f"{'script' if has_script else 'no-script':9} last_items={items}"
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
        site.slug, crawl_fn, ctx, store, news, first_crawl_cap=config.first_crawl_cap()
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
    news = NewsClient()
    try:
        for site in _crawl_targets(args):
            summary = crawl_one(site, store=store, news=news)
            print(
                f"{summary.site}: found={summary.items_found} "
                f"new={summary.new_items} errors={summary.errors}"
            )
    finally:
        store.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    news = NewsClient()
    sites = load_sites(config.sites_path())

    async def crawl(site: Site) -> CrawlSummary:
        return await asyncio.to_thread(crawl_one, site, store=store, news=news)

    scheduler = Scheduler(sites, store=store, crawl=crawl)

    async def serve() -> None:
        await scheduler.run(stop=asyncio.Event())

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
