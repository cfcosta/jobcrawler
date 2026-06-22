"""Operator CLI (§10): ``autocrawl run | generate | validate | crawl | promote | list | status``.

``run`` is the primary deployment entrypoint (the service schedules itself, §4.9);
``crawl`` is for manual one-shot runs and CI; ``validate`` dry-runs a committed
script through the §4.4 sandbox with **no ingest**; ``generate`` runs the RLM and
gates before writing ``crawlers/<slug>.py`` (it needs a configured LM + Deno, so
its heavy deps are imported lazily); ``promote`` moves a self-heal crawler staged
under ``crawlers/_pending`` (§14.2) to live; ``list`` shows sites, script presence,
and last-run health; ``status`` is the M4 observability read — per-site run
aggregates, fleet health distribution, and observed drift-recovery MTTR. The
list/validate/crawl/status wiring is thin over already-tested backends; the live
paths are correct-by-construction.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import logging
import os
import signal
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from bizup_autocrawler import config
from bizup_autocrawler.generate.scoring import CandidateResult
from bizup_autocrawler.governor import RateGovernor
from bizup_autocrawler.health import (
    HealthState,
    RegenOutcome,
    SelfHealer,
    staleness_candidate,
)
from bizup_autocrawler.ingest import NewsClient
from bizup_autocrawler.metrics import (
    aggregate_runs,
    drift_mttr,
    health_distribution,
    recovery_events,
)
from bizup_autocrawler.runtime.runner import (
    CrawlSummary,
    crawl_site_isolated,
    probe_isolated,
)
from bizup_autocrawler.runtime.sandbox import validate_crawler
from bizup_autocrawler.schedule import Scheduler, Site
from bizup_autocrawler.sites import find_site, load_sites
from bizup_autocrawler.state import RunRecord, StateStore
from bizup_autocrawler.trainset import record_failure
from bizup_autocrawler.urls import normalize_host

_CRAWLERS_DIR = Path(config.crawlers_dir())

_log = logging.getLogger("bizup_autocrawler.cli")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_logging() -> None:
    """Route the daemon/CLI's structured logs to stderr at the configured level (§4.9).

    Without this the run loop's INFO heartbeat — each crawl's item counts, the regen
    sweep tally — is swallowed and a production daemon looks dead. ``basicConfig`` is a
    no-op once the root logger has handlers, so we also set our package logger's level
    explicitly to honor ``AUTOCRAWLER_LOG_LEVEL`` even on a second entry.
    """
    level = config.log_level()
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    logging.getLogger("bizup_autocrawler").setLevel(level)


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
    """The health column: a persisted terminal state wins over a re-derived stale.

    A generation-failed or pending-review site still *looks* like a staleness
    candidate from its run history, so re-deriving would mislabel it "stale" (implying
    it self-heals); the persisted state (§4.8, §14.2) is authoritative — gen-failed
    needs an operator, pending-review awaits ``autocrawl promote``.
    """
    persisted = store.get_health(slug)
    if persisted == HealthState.GENERATION_FAILED.value:
        return "gen-failed"
    if persisted == HealthState.PENDING_REVIEW.value:
        return "pending-review"
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


# -- status ------------------------------------------------------------------


def _coerce_health(value: str | None) -> HealthState:
    """Map a persisted health string (or None) to a HealthState for the fleet tally.

    A site with no recorded health has never been assessed by the §4.8 sweep; absent
    any recorded problem it counts as healthy-by-default. An unrecognized string (an
    older schema value) likewise degrades to healthy rather than crashing the surface.
    """
    if value is None:
        return HealthState.HEALTHY
    try:
        return HealthState(value)
    except ValueError:
        return HealthState.HEALTHY


def render_status(sites: Sequence[Site], store: StateStore) -> list[str]:
    """Operator status surface (M4): per-site run totals, fleet health, drift MTTR.

    Surfaces the otherwise-unwired metrics.py: ``aggregate_runs`` folds each site's run
    history (so an operator reads the fleet without touching the DB), ``health_distribution``
    tallies the persisted §4.8 states, and ``drift_mttr`` summarizes recovery time over
    drift events reconstructed from run history (``recovery_events``). The MTTR is
    labeled *observed* — with no health-transition log it measures item-level
    dark→recovered, a superset of §4.8's "gate-passing replacement enabled".
    """
    lines: list[str] = []
    states: list[HealthState] = []
    events: list[tuple[datetime, datetime]] = []
    for site in sites:
        history = store.runs_for(site.slug)
        agg = aggregate_runs(history)
        health = _coerce_health(store.get_health(site.slug))
        states.append(health)
        events.extend(recovery_events(history))
        last = (
            agg.last_finished.isoformat(timespec="seconds")
            if agg.last_finished is not None
            else "never"
        )
        lines.append(
            f"{site.slug:24} health={health.value:<16} "
            f"runs={agg.runs} found={agg.items_found} new={agg.new_items} "
            f"errors={agg.errors} err_rate={agg.error_rate:.2f} last={last}"
        )
    dist = health_distribution(states)
    lines.append(
        "fleet: " + " ".join(f"{name}={count}" for name, count in dist.items())
    )
    mttr = drift_mttr(events)
    lines.append(f"drift recovery MTTR (observed): {'n/a' if mttr is None else mttr}")
    return lines


# -- validate ----------------------------------------------------------------


def validate_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> CandidateResult:
    """Dry-run a committed script through the sandbox — no ingest (§10)."""
    source = _crawler_file(slug, crawlers_dir).read_text()
    host = normalize_host(_site_url_of(source))
    return validate_crawler(source, allowed_hosts={host} if host else set())


# -- crawl -------------------------------------------------------------------


def crawl_one(
    site: Site,
    *,
    store: StateStore,
    news: NewsClient,
    governor: RateGovernor | None = None,
    crawlers_dir: Path = _CRAWLERS_DIR,
) -> CrawlSummary:
    """Run the committed crawler in a bounded subprocess, then ingest host-side (§4.4).

    The committed script runs unattended on the production schedule, so it executes in
    the §4.4 child (AST gate + RLIMIT_AS / RLIMIT_CPU + wall-clock): a runaway crawler
    (infinite loop, memory blowup) is bounded and reported as a failed run, never
    allowed to wedge or OOM the daemon and take the whole fleet down with it. The
    child only fetches + yields; the host keeps dedup, the completeness filter, and
    governor-paced ingest. ``governor`` is the shared §4.7 ingest pacer; the caller
    threads one instance through every site so the news-API cap holds globally across
    concurrent crawls (a per-call governor would multiply it by fan-out).
    """
    source = _crawler_file(site.slug, crawlers_dir).read_text()
    host = normalize_host(_site_url_of(source))
    return crawl_site_isolated(
        site.slug,
        source,
        {host} if host else set(),
        store,
        news,
        timeout=config.crawl_timeout(),  # wall-clock budget (§4.4)
        mem_mb=config.crawl_mem_mb(),  # RLIMIT_AS
        cpu_seconds=config.crawl_cpu_seconds(),  # RLIMIT_CPU (decoupled from wall clock)
        first_crawl_cap=config.first_crawl_cap(),
        governor=governor,
        min_body_chars=config.min_body_chars(),  # only ingest complete (§4.6)
        paywall_markers=config.paywall_markers(),  # drop paywall teasers (§4.6)
        crawler_name=site.name,  # attribute each item to its source crawler
        crawler_url=site.url,
    )


# -- startup preflight -------------------------------------------------------


class PreflightError(Exception):
    """A run/crawl startup precondition (config / registry / state) failed (§4.9).

    Raised with an operator-actionable message so a misconfiguration fails fast at
    boot instead of surfacing as an opaque error deep in the run loop.
    """


def _valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _state_writability(db_path: str) -> str | None:
    """Return a reason the state path can't be written, or None if it's fine."""
    if db_path == ":memory:" or db_path.startswith("file::memory:"):
        return None  # an in-memory store is always writable
    target = Path(db_path)
    parent = target.parent
    if not parent.exists():
        return f"state directory {parent} does not exist"
    if not os.access(parent, os.W_OK):
        return f"state directory {parent} is not writable"
    if target.exists() and not os.access(target, os.W_OK):
        return f"state file {target} is not writable"
    return None


def preflight(
    *,
    sites_path: str | None = None,
    db_path: str | None = None,
    news_url: str | None = None,
) -> list[Site]:
    """Validate run/crawl startup preconditions, returning the loaded registry (§4.9).

    Fails fast (``PreflightError`` with an actionable message) on a missing or
    malformed ``sites.yaml``, an invalid site entry, a malformed ``NEWS_API_URL``, or
    an unwritable state path — the opaque mid-run failures these would otherwise
    become. An empty *enabled* registry is a warning, not fatal (an operator may be
    staging the list); the daemon would simply idle.
    """
    sites_path = sites_path or config.sites_path()
    db_path = db_path or _db_path()
    news_url = news_url or config.news_api_url()

    try:
        sites = load_sites(sites_path)
    except FileNotFoundError as exc:
        raise PreflightError(
            f"site registry not found at {sites_path} "
            "(set AUTOCRAWLER_SITES or create the file)"
        ) from exc
    except yaml.YAMLError as exc:
        raise PreflightError(
            f"site registry {sites_path} is not valid YAML: {exc}"
        ) from exc
    except ValidationError as exc:
        raise PreflightError(
            f"site registry {sites_path} has an invalid entry: {exc}"
        ) from exc

    if not any(s.enabled for s in sites):
        _log.warning(
            "preflight: no enabled sites in %s — the daemon will idle", sites_path
        )

    if not _valid_http_url(news_url):
        raise PreflightError(
            f"NEWS_API_URL {news_url!r} is not a valid http(s) URL "
            "(expected e.g. http://localhost:8000)"
        )

    reason = _state_writability(db_path)
    if reason is not None:
        raise PreflightError(f"state store is not writable: {reason}")

    return sites


def _preflight_or_exit() -> tuple[list[Site] | None, int]:
    """Run the startup preflight; on failure log it and return a nonzero exit code."""
    try:
        return preflight(), 0
    except PreflightError as exc:
        _log.error("startup preflight failed: %s", exc)
        return None, 2


# -- command handlers --------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    try:
        for line in render_list(load_sites(config.sites_path()), store):
            print(line)
    finally:
        store.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    # Read-only M4 observability surface: per-site run aggregates, fleet health
    # distribution, and observed drift-recovery MTTR — the numbers an operator reads
    # without touching the state DB. Pure read; opens the store, renders, closes.
    store = StateStore(_db_path())
    try:
        for line in render_status(load_sites(config.sites_path()), store):
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
    _configure_logging()  # surface runtime warnings (robots/politeness) on one-shot runs
    _, code = _preflight_or_exit()  # fail fast on bad config before opening the store
    if code != 0:
        return code
    store = StateStore(_db_path())
    news = NewsClient(timeout=config.ingest_timeout())
    governor = RateGovernor(
        config.ingest_rate()
    )  # one shared pacer for the batch (§4.7)
    try:
        for site in _crawl_targets(args):
            summary = crawl_one(site, store=store, news=news, governor=governor)
            print(
                f"{summary.site}: found={summary.items_found} "
                f"new={summary.new_items} incomplete={summary.incomplete} "
                f"errors={summary.errors}"
            )
    finally:
        store.close()
    return 0


def _probe(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> int:
    """Re-run a committed crawler in the bounded subprocess, counting complete items (§4.8).

    The §4.8 re-probe the self-healer uses to tell a real failure from a transient
    outage. It runs through the same §4.4 isolation as the live crawl — a drifted
    crawler can hang on the reprobe too, and that hang must be bounded, not block the
    heal loop — with no ingest and no state. A site whose committed script is missing
    or fails the gate probes to 0 (a confirmed failure → regenerate), never an
    exception that aborts the loop.
    """
    try:
        source = _crawler_file(slug, crawlers_dir).read_text()
    except OSError:
        return 0  # no committed script → confirmed failure, not a transient blip
    host = normalize_host(_site_url_of(source))
    return probe_isolated(
        source,
        {host} if host else set(),
        min_body_chars=config.min_body_chars(),
        paywall_markers=config.paywall_markers(),
        timeout=config.crawl_timeout(),
        mem_mb=config.crawl_mem_mb(),
        cpu_seconds=config.crawl_cpu_seconds(),
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

    async def regenerate(slug: str) -> RegenOutcome:
        from bizup_autocrawler.cli_generate import regenerate_one

        # regenerate_one honors AUTOCRAWLER_REVIEW_GATE: by default it stages the new
        # crawler (RegenOutcome.STAGED → pending-review, §14.2) rather than going live.
        return await asyncio.to_thread(regenerate_one, slug)

    return SelfHealer(
        store,
        reprobe=reprobe,
        regenerate=regenerate,
        on_confirmed_failure=lambda slug: _record_site_failure(sites, slug),
    )


def cmd_run(args: argparse.Namespace) -> int:
    _configure_logging()  # the daemon must not run silent (§4.9)
    sites, code = _preflight_or_exit()  # fail fast on bad config, never mid-loop
    if sites is None:
        return code
    store = StateStore(_db_path())
    news = NewsClient(timeout=config.ingest_timeout())

    # A prior daemon killed mid-crawl leaves finished=NULL run rows; the scheduler
    # would treat those sites as perpetually in-flight and never re-dispatch them.
    # Reap before the scheduler starts (single-process §4.9: nothing is in-flight yet).
    reaped = store.reap_orphans()
    if reaped:
        _log.warning("reaped %d orphaned run row(s) from a prior crash", reaped)

    # One shared, thread-safe pacer (§4.7): the scheduler runs up to `concurrency`
    # crawls at once on to_thread workers, so a per-crawl governor would let the
    # global news-API ingest cap be exceeded by the fan-out factor.
    governor = RateGovernor(config.ingest_rate())

    async def crawl(site: Site) -> CrawlSummary:
        return await asyncio.to_thread(
            crawl_one, site, store=store, news=news, governor=governor
        )

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


def cmd_promote(args: argparse.Namespace) -> int:
    # Promote a self-heal candidate staged under crawlers/_pending (§14.2) to live.
    # promote_one is pure filesystem, but it lives beside generate (shared crawlers/
    # paths), so import it lazily to keep cli.py free of the dspy-heavy module at start.
    from bizup_autocrawler.cli_generate import promote_one

    ok, message = promote_one(args.slug)
    print(message)
    return 0 if ok else 1


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

    prom = sub.add_parser(
        "promote", help="promote a staged self-heal crawler to live (§14.2)"
    )
    prom.add_argument("slug")
    prom.set_defaults(func=cmd_promote)

    sub.add_parser("list", help="show sites, script presence, health").set_defaults(
        func=cmd_list
    )

    sub.add_parser(
        "status", help="show per-site run aggregates, fleet health, drift MTTR (M4)"
    ).set_defaults(func=cmd_status)

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
