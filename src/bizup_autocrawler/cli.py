"""Operator CLI (§10): ``run | generate | validate | crawl | promote | rollback | list | status``.

``run`` is the primary deployment entrypoint (the service schedules itself, §4.9);
``crawl`` is for manual one-shot runs and CI; ``validate`` dry-runs a committed
script through the §4.4 sandbox with **no ingest**; ``generate`` runs the RLM and
gates before writing ``crawlers/<slug>.py`` (it needs a configured LM + Deno, so
its heavy deps are imported lazily); ``promote`` moves a self-heal crawler staged
under ``crawlers/_pending`` (§14.2) to live; ``list`` shows sites, script presence,
last-run health, and the §4.9 learned crawl cadence; ``status`` is the M4
observability read — per-site run aggregates + drift-transition counts, fleet health
distribution, drift-recovery MTTR, and the §4.11 self-improvement state (active
generator + held-out score, any adoption probation, and the autonomy brake). The
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

from bizup_autocrawler import config, generator_pointer
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
    mean_body_quality,
    recovery_events,
    transition_events,
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


def _has_live_crawler(slug: str, crawlers_dir: Path = _CRAWLERS_DIR) -> bool:
    """True if a committed live crawler exists for the slug (else a §4.2 cold-start).

    The run loop and the self-healer share this to tell a crawlable site from one that
    still needs its first crawler written. A crawler staged under ``_pending`` does not
    count — it isn't live yet — so a site stays in the bootstrap path until promoted.
    """
    return _crawler_file(slug, crawlers_dir).exists()


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


def _humanize_secs(secs: float) -> str:
    """A compact human form for a learned interval (e.g. ``5400.0`` → ``"1.5h"``).

    The adaptive controller learns arbitrary float intervals, so this rounds to the
    largest sensible unit for the ``list`` surface rather than printing raw seconds."""
    if secs < 60:
        return f"{secs:.0f}s"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if secs >= size:
            return f"{secs / size:.1f}".rstrip("0").rstrip(".") + unit
    return f"{secs:.0f}s"  # unreachable (secs >= 60 always matches above)


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


def _cadence_label(store: StateStore, site: Site) -> str:
    """The site's crawl cadence for the ``list`` surface (§4.9 adaptive observability).

    A learned interval (the AIMD hit-rate controller has written one) wins and is shown
    humanized; an adaptive source with no learned value yet reads ``adaptive`` (it falls
    back to a seed until the first crawl); a fixed-cadence source reads ``fixed``."""
    interval = store.get_interval(site.slug)
    if interval is not None:
        return _humanize_secs(interval)
    return "adaptive" if site.adaptive else "fixed"


def render_list(
    sites: Sequence[Site], store: StateStore, *, crawlers_dir: Path = _CRAWLERS_DIR
) -> list[str]:
    """One line per site: enabled flag, script presence, last item count, health, cadence.

    ``every=`` surfaces the §4.9 learned adaptive interval (or ``adaptive``/``fixed``)
    so an operator can see at a glance how often each source is actually being polled."""
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
            f"last_items={items:>4} health={health:<14} "
            f"every={_cadence_label(store, site)}"
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


def _generator_line() -> str:
    """The active §4.11 generator version + held-out score, plus any probation note.

    Surfaces the auto-adoption state an operator otherwise can't see: which versioned
    artifact the live daemon resolves (or ``baseline``), the held-out margin it cleared
    on adoption, and — when the P4c regression watcher is still holding it under
    probation — that the daemon may yet auto-roll it back."""
    active = generator_pointer.active_generator_path()
    if active is None:
        line = "generator: baseline"
    else:
        score = generator_pointer.active_generator_meta().get("heldout_score")
        score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
        line = f"generator: {Path(active).name} heldout={score_str}"
    marker = generator_pointer.read_adoption()
    if marker is not None:
        line += (
            f" (under probation, adopted "
            f"{marker.adopted_at.isoformat(timespec='seconds')})"
        )
    return line


def render_status(sites: Sequence[Site], store: StateStore) -> list[str]:
    """Operator status surface (M4): per-site run totals, fleet health, drift MTTR.

    Surfaces the otherwise-unwired metrics.py: ``aggregate_runs`` folds each site's run
    history (so an operator reads the fleet without touching the DB), ``health_distribution``
    tallies the persisted §4.8 states, and ``drift_mttr`` summarizes recovery time.

    MTTR prefers the durable health-transition log (``transition_events``) — the
    *measured* §4.8 metric ("previously-healthy site going dark → gate-passing
    replacement enabled"). When no transitions are recorded yet (e.g. a DB predating
    the log), it falls back to reconstructing item-level dark→recovered from run
    history (``recovery_events``), labeled *observed* to flag the approximation.

    Each per-site line also carries ``transitions=`` — how many health-log rows the
    site has accrued, i.e. how churny its drift lifecycle has been. Two fleet-wide
    footer lines surface the §4.11 self-improvement state: the active generator
    (version + held-out score, plus any probation the P4c watcher still holds it under)
    and the master ``autonomy`` brake (``on``/``OFF``).
    """
    lines: list[str] = []
    states: list[HealthState] = []
    logged: list[tuple[datetime, datetime]] = []
    reconstructed: list[tuple[datetime, datetime]] = []
    have_log = False
    for site in sites:
        history = store.runs_for(site.slug)
        agg = aggregate_runs(history)
        health = _coerce_health(store.get_health(site.slug))
        states.append(health)
        transitions = store.transitions_for(site.slug)
        if transitions:
            have_log = True
        logged.extend(transition_events(transitions))
        reconstructed.extend(recovery_events(history))
        last = (
            agg.last_finished.isoformat(timespec="seconds")
            if agg.last_finished is not None
            else "never"
        )
        bq = mean_body_quality(history)
        bq_str = "n/a" if bq is None else f"{bq:.2f}"
        lines.append(
            f"{site.slug:24} health={health.value:<16} "
            f"runs={agg.runs} found={agg.items_found} new={agg.new_items} "
            f"errors={agg.errors} errs/run={agg.error_rate:.2f} "
            f"body_q={bq_str} transitions={len(transitions)} last={last}"
        )
    dist = health_distribution(states)
    lines.append(
        "fleet: " + " ".join(f"{name}={count}" for name, count in dist.items())
    )
    if have_log:
        mttr = drift_mttr(logged)
        lines.append(f"drift recovery MTTR: {'n/a' if mttr is None else mttr}")
    else:
        mttr = drift_mttr(reconstructed)
        lines.append(
            f"drift recovery MTTR (observed): {'n/a' if mttr is None else mttr}"
        )
    # Fleet-wide self-improvement state (§4.11): the active generator + held-out score,
    # any probation the watcher is still holding it under, and whether the master
    # autonomy brake is engaged (OFF = the daemon is crawling but self-modification —
    # heal/regen/promote/reopt/adopt/rollback — is frozen).
    lines.append(_generator_line())
    lines.append(f"autonomy: {'on' if config.autonomy() else 'OFF'}")
    return lines


# -- validate ----------------------------------------------------------------


def validate_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> CandidateResult:
    """Dry-run a committed script through the sandbox — no ingest (§10).

    ``polite=True`` runs it through the §4.5 production ctx (per-host pacer + robots),
    so the dry-run is a faithful preview of the scheduled crawl rather than a bare-ctx
    fetch that could over-collect paths production's robots policy would refuse.
    """
    source = _crawler_file(slug, crawlers_dir).read_text()
    host = normalize_host(_site_url_of(source))
    return validate_crawler(
        source, allowed_hosts={host} if host else set(), polite=True
    )


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

        # regenerate_one honors the tri-state AUTOCRAWLER_REVIEW_GATE: `on` (default)
        # stages the crawler (RegenOutcome.STAGED → pending-review, §14.2); `auto`
        # backs up + promotes it live (reversible); `off` writes live with no backup.
        return await asyncio.to_thread(regenerate_one, slug)

    async def promote(slug: str) -> bool:
        # Auto-clear a pending-review strand (§4.8): move the staged crawler live when
        # the gate is no longer `on`. Pure filesystem, run off the loop like the rest.
        from bizup_autocrawler.cli_generate import promote_one

        ok, _ = await asyncio.to_thread(promote_one, slug)
        return ok

    return SelfHealer(
        store,
        reprobe=reprobe,
        regenerate=regenerate,
        on_confirmed_failure=lambda slug: _record_site_failure(sites, slug),
        body_quality_floor=config.body_quality_floor(),
        genfail_backoff=config.genfail_backoff(),
        genfail_backoff_cap=config.genfail_backoff_cap(),
        review_mode=config.review_gate,
        promote=promote,
        has_crawler=_has_live_crawler,  # §4.2 cold-start: write a first crawler if none
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
        sites,
        store=store,
        crawl=crawl,
        healer=_build_healer(store, sites),
        has_crawler=_has_live_crawler,  # route crawler-less sites to §4.2 bootstrap
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


def cmd_rollback(args: argparse.Namespace) -> int:
    # Restore a site's prior crawler from its crawlers/_history backup — the undo for
    # an `auto` self-heal promotion (§4.8). Pure filesystem, but it shares crawlers/
    # paths with generate, so import it lazily to keep cli.py free of dspy at start.
    from bizup_autocrawler.cli_generate import rollback_one

    ok, message = rollback_one(args.slug)
    print(message)
    return 0 if ok else 1


def cmd_optimize(args: argparse.Namespace) -> int:
    # The offline §4.11 self-improvement job (build trainset → optimize → promote);
    # its deps (dspy + the sandbox) are heavy, so import lazily.
    from bizup_autocrawler.cli_optimize import optimize_command

    return optimize_command(args)


def cmd_rollback_generator(args: argparse.Namespace) -> int:
    # Undo the last generator adoption (the §4.11 versioned rollback). Flips the
    # active-generator pointer back to the prior version (or baseline); cheap, no LM.
    from bizup_autocrawler import generator_pointer

    ok, restored = generator_pointer.rollback_active()
    if not ok:
        print("rollback-generator: no prior generator adoption to roll back to")
        return 1
    # The rolled-back adoption is no longer the one on probation: consume its marker
    # so the §4.11 auto-rollback watcher doesn't keep judging (and re-rolling) a
    # generator the operator just reverted. Mirrors the watcher's own rollback path.
    generator_pointer.clear_adoption()
    if restored is None:
        print("rolled back generator to baseline (pointer cleared)")
    else:
        print(f"rolled back active generator to {restored}")
    return 0


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

    rb = sub.add_parser(
        "rollback", help="revert a site to its prior crawler from _history (§4.8)"
    )
    rb.add_argument("slug")
    rb.set_defaults(func=cmd_rollback)

    sub.add_parser("list", help="show sites, script presence, health").set_defaults(
        func=cmd_list
    )

    sub.add_parser(
        "status", help="show per-site run aggregates, fleet health, drift MTTR (M4)"
    ).set_defaults(func=cmd_status)

    opt = sub.add_parser(
        "optimize",
        help="offline §4.11 self-improvement: build trainset, optimize, promote",
    )
    opt.add_argument(
        "--adopt",
        action="store_true",
        help="on promotion, flip the active-generator pointer to the new artifact "
        "(§4.11 auto-adopt; reversible via rollback-generator)",
    )
    opt.set_defaults(func=cmd_optimize)

    sub.add_parser(
        "rollback-generator",
        help="revert the active generator to the previously-adopted version (§4.11)",
    ).set_defaults(func=cmd_rollback_generator)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
