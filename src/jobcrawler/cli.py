"""Operator CLI (§10): ``run | serve | generate | validate | crawl | promote | rollback |
list | status | positions | matches | rematch | profile | login``.

``run`` is the primary deployment entrypoint (the service schedules itself, §4.9);
``crawl`` is for manual one-shot runs and CI; ``validate`` dry-runs a committed
script through the §4.4 sandbox with **no upsert**; ``generate`` runs the RLM and
gates before writing ``crawlers/<slug>.py`` (it needs a configured LM + Deno, so
its heavy deps are imported lazily); ``promote`` moves a self-heal crawler staged
under ``crawlers/_pending`` (§14.2) to live; ``list`` shows sites, script presence,
last-run health, and the §4.9 learned crawl cadence; ``status`` is the M4
observability read — per-site run aggregates + drift-transition counts, fleet health
distribution, drift-recovery MTTR, the §4.7 position lifecycle tallies + §4.7 match
spend, and the §4.11 self-improvement state.

The job-tracking surfaces read the §4.7 ``positions`` store host-side: ``positions``
lists open jobs, ``matches`` ranks the scored ones by fit, ``rematch`` drains the
pending-match backlog and re-evaluates verdicts after a ``PROFILE.md`` edit, and
``profile`` pretty-prints the active candidate profile. The list/validate/crawl/status
wiring is thin over already-tested backends; the live paths are correct-by-construction.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml
from pydantic import ValidationError

from jobcrawler import config, generator_pointer, notify
from jobcrawler.generate.scoring import CandidateResult
from jobcrawler.governor import RateGovernor
from jobcrawler.health import (
    HealthState,
    RegenOutcome,
    SelfHealer,
    staleness_candidate,
)
from jobcrawler.metrics import (
    aggregate_runs,
    drift_mttr,
    health_distribution,
    mean_body_quality,
    recovery_events,
    transition_events,
)
from jobcrawler.profile import ProfileNotFound, load_profile
from jobcrawler.runtime.contract import allowed_hosts_of
from jobcrawler.runtime.live_ctx import build_recheck_fetch
from jobcrawler.runtime.runner import (
    CrawlSummary,
    crawl_site_isolated,
    probe_isolated,
)
from jobcrawler.runtime.sandbox import validate_crawler
from jobcrawler.schedule import Scheduler, Site
from jobcrawler.sites import find_site, load_sites
from jobcrawler.state import PositionRecord, RunRecord, StateStore
from jobcrawler.trainset import record_failure

if TYPE_CHECKING:
    from jobcrawler.match import Matcher

_CRAWLERS_DIR = Path(config.crawlers_dir())

_log = logging.getLogger("jobcrawler.cli")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_logging() -> None:
    """Route the daemon/CLI's structured logs to stderr at the configured level (§4.9).

    Without this the run loop's INFO heartbeat — each crawl's position counts, the regen
    sweep tally — is swallowed and a production daemon looks dead. ``basicConfig`` is a
    no-op once the root logger has handlers, so we also set our package logger's level
    explicitly to honor ``JOBCRAWLER_LOG_LEVEL`` even on a second entry.
    """
    level = config.log_level()
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    logging.getLogger("jobcrawler").setLevel(level)


def _crawler_file(slug: str, crawlers_dir: Path = _CRAWLERS_DIR) -> Path:
    return crawlers_dir / f"{slug}.py"


def _has_live_crawler(slug: str, crawlers_dir: Path = _CRAWLERS_DIR) -> bool:
    """True if a committed live crawler exists for the slug (else a §4.2 cold-start).

    The run loop and the self-healer share this to tell a crawlable site from one that
    still needs its first crawler written. A crawler staged under ``_pending`` does not
    count — it isn't live yet — so a site stays in the bootstrap path until promoted.
    """
    return _crawler_file(slug, crawlers_dir).exists()


def _db_path() -> str:
    url = config.state_url()
    return url.removeprefix("sqlite:///") if url.startswith("sqlite:") else url


def _acquire_run_lock(path: str | None = None) -> int | None:
    """Take the exclusive single-instance daemon lock (``flock``); ``None`` if held.

    Only one ``jobcrawl run`` may drive a store at a time: :func:`cmd_run` reaps every
    unfinished run row as a crash orphan on that single-process assumption (§4.9), so a
    second concurrent daemon would reap the first's in-flight runs and double-dispatch
    every site. The lock rides on the open fd (kept for the process lifetime), so a
    crash releases it — no stale lock to reap. Mirrors ``optimize_job.optimize_lock``;
    see that module's docstring on why ``flock`` (not an ``fcntl`` record lock that
    drops on any ``close``) and not a PID file."""
    lock_path = path or config.run_lock_path()
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


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
    needs an operator, pending-review awaits ``jobcrawl promote``.
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
    """One line per site: enabled flag, script presence, last position count, health, cadence.

    ``every=`` surfaces the §4.9 learned adaptive interval (or ``adaptive``/``fixed``)
    so an operator can see at a glance how often each source is actually being polled."""
    rows: list[str] = []
    for site in sites:
        has_script = _crawler_file(site.slug, crawlers_dir).exists()
        history = store.runs_for(site.slug)
        last = history[0] if history else None
        positions = str(last.items_found) if last is not None else "-"
        health = _health_label(store, site.slug, history, last is not None)
        rows.append(
            f"{site.slug:24} {'on' if site.enabled else 'off':3} "
            f"{'script' if has_script else 'no-script':9} "
            f"positions={positions:>4} health={health:<14} "
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


def _positions_block(store: StateStore) -> list[str]:
    """The §4.7 job-tracking footer for ``status``: lifecycle tallies, match spend
    against the daily cap, and the current strongest match as a teaser.

    Fleet-wide (not per-site): ``position_counts`` folds the whole ``positions`` table,
    ``match_spent`` reads today's token draw against ``match_daily_cap`` (the §8 budget
    backstop), and ``matches`` surfaces the single best open+scored fit so an operator
    reading ``status`` sees whether anything worth acting on has landed.
    """
    counts = store.position_counts()
    lines = [
        f"positions: open={counts['open']} closed={counts['closed']} "
        f"matched={counts['matched']} pending={counts['pending']}"
    ]
    day = datetime.now(timezone.utc).date().isoformat()
    lines.append(
        f"match spend: {store.match_spent(day)}/{config.match_daily_cap()} today"
    )
    top = store.matches(config.match_min_score(), limit=1)
    if top:
        best = top[0]
        lines.append(f"top match: [{best.match_score}] {best.title} @ {best.company}")
    else:
        lines.append("top match: none")
    return lines


def render_status(sites: Sequence[Site], store: StateStore) -> list[str]:
    """Operator status surface (M4): per-site run totals, fleet health, drift MTTR, the
    §4.7 position lifecycle + §4.7 match spend, and the §4.11 self-improvement state.

    Surfaces the otherwise-unwired metrics.py: ``aggregate_runs`` folds each site's run
    history (so an operator reads the fleet without touching the DB), ``health_distribution``
    tallies the persisted §4.8 states, and ``drift_mttr`` summarizes recovery time.

    MTTR prefers the durable health-transition log (``transition_events``) — the
    *measured* §4.8 metric ("previously-healthy site going dark → gate-passing
    replacement enabled"). When no transitions are recorded yet (e.g. a DB predating
    the log), it falls back to reconstructing item-level dark→recovered from run
    history (``recovery_events``), labeled *observed* to flag the approximation.

    Each per-site line also carries ``transitions=`` — how many health-log rows the
    site has accrued. A job-tracking block (open/closed/matched/pending positions,
    today's match spend vs the daily cap, and the top open match) and two fleet-wide
    footer lines surface the §4.11 self-improvement state: the active generator and the
    master ``autonomy`` brake (``on``/``OFF``).
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
    # §4.7 job-tracking block: position lifecycle tallies, match spend vs the daily
    # cap, and the strongest open match — the numbers a jobs operator actually reads.
    lines.extend(_positions_block(store))
    # Fleet-wide self-improvement state (§4.11): the active generator + held-out score,
    # any probation the watcher is still holding it under, and whether the master
    # autonomy brake is engaged (OFF = the daemon is crawling but self-modification —
    # heal/regen/promote/reopt/adopt/rollback — is frozen).
    lines.append(_generator_line())
    lines.append(f"autonomy: {'on' if config.autonomy() else 'OFF'}")
    return lines


# -- positions / matches -----------------------------------------------------


def _trunc(text: str | None, width: int) -> str:
    """Truncate to ``width`` display cells (ellipsis on overflow), for aligned columns."""
    text = text or ""
    return text if len(text) <= width else text[: width - 1] + "…"


def _humanize_age(dt: datetime | None, *, now: datetime | None = None) -> str:
    """A compact age (``2h``/``3d``) since ``dt``; ``-`` when unknown, ``0s`` if future."""
    if dt is None:
        return "-"
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (now - dt).total_seconds()
    return _humanize_secs(max(0.0, secs))


def render_positions(rows: Sequence[PositionRecord]) -> list[str]:
    """Aligned columns — score, title, company, location, workplace, age — one per job.

    ``score`` is the §4.7 match score (``-`` when unscored); ``age`` counts from the
    posting's ``posted_at`` when the source stated one, else from ``first_seen``.
    """
    out: list[str] = []
    for p in rows:
        score = "-" if p.match_score is None else str(p.match_score)
        out.append(
            f"{score:>4}  {_trunc(p.title, 34):34}  {_trunc(p.company, 20):20}  "
            f"{_trunc(p.location, 18):18}  {(p.workplace or '-'):8}  "
            f"{_humanize_age(p.posted_at or p.first_seen):>5}"
        )
    return out


def _reasons_text(raw: str | None) -> str:
    """Render the stored match reasons (a JSON list) as a compact ``; ``-joined string."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    if isinstance(data, list):
        return "; ".join(str(x) for x in data)
    return str(data)


def render_matches(rows: Sequence[PositionRecord]) -> list[str]:
    """Top matches: ``[score] verdict Title @ Company — reasons`` (best fit first)."""
    out: list[str] = []
    for p in rows:
        reasons = _reasons_text(p.match_reasons)
        head = (
            f"[{p.match_score:>3}] {(p.match_verdict or '-'):7} "
            f"{_trunc(p.title, 40)} @ {p.company}"
        )
        out.append(head + (f"  — {reasons}" if reasons else ""))
    return out


# -- validate ----------------------------------------------------------------


def validate_one(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> CandidateResult:
    """Dry-run a committed script through the sandbox — no upsert (§10).

    ``polite=True`` runs it through the §4.5 production ctx (per-host pacer + robots),
    so the dry-run is a faithful preview of the scheduled crawl rather than a bare-ctx
    fetch that could over-collect paths production's robots policy would refuse.
    """
    source = _crawler_file(slug, crawlers_dir).read_text()
    # SITE + declared EXTRA_HOSTS (§8): exactly the allowlist admission gated.
    return validate_crawler(source, allowed_hosts=allowed_hosts_of(source), polite=True)


# -- crawl -------------------------------------------------------------------


class _MatchPacer:
    """Adapt a :class:`RateGovernor` (``.wait``) to the matcher's governor protocol.

    The §4.7 matcher paces its LLM calls through a ``.pace()`` method; the shared
    :class:`RateGovernor` exposes ``.wait()``. This thin adapter lets the daemon hand
    the matcher the same rate-limiter grammar (``JOBCRAWLER_MATCH_RATE``) the crawl
    side uses, without the matcher depending on the governor's concrete API.
    """

    def __init__(self, governor: RateGovernor) -> None:
        self._governor = governor

    def pace(self) -> None:
        self._governor.wait()


def build_matcher(store: StateStore) -> Matcher | None:
    """Construct the §4.7 host-side profile matcher from ``PROFILE.md``, or None (crawl-only).

    A missing ``PROFILE.md`` (``ProfileNotFound``) is a valid crawl-only mode: we warn
    and return None so the crawl still runs, just without match scoring. A configured
    but unusable match model likewise degrades to crawl-only rather than aborting — the
    matcher is best-effort and must never break the crawl loop (§4.7/§8). A *malformed*
    profile is caught up-front by :func:`preflight`; were it reached here it would raise.
    """
    from jobcrawler.match import (
        Matcher,
    )  # lazy: pulls dspy, unwanted for `list`/`status`

    try:
        profile = load_profile(config.profile_path())
    except ProfileNotFound as exc:
        _log.warning("no PROFILE.md — crawling without match scoring: %s", exc)
        return None
    try:
        model = config.require_match_model()
    except RuntimeError as exc:
        _log.warning("no match model — crawling without match scoring: %s", exc)
        return None
    # Match-time salary/employer extraction (§4.7) rides the cheap tier and is OFF when no
    # such tier exists (require_extract_model raises) — extraction is a best-effort add-on,
    # never a reason to break or block scoring.
    try:
        extract_model: str | None = config.require_extract_model()
    except RuntimeError:
        extract_model = None
    return Matcher(
        store,
        profile,
        model=model,
        batch=config.match_batch(),
        daily_cap=config.match_daily_cap(),
        governor=_MatchPacer(RateGovernor(config.match_rate())),
        clock=lambda: datetime.now(timezone.utc),
        max_tokens=config.match_max_tokens(),
        drain_floor=config.match_drain_floor(),
        extract_model=extract_model,
        extract_min_score=config.extract_min_score(),
        extract_cap=config.extract_cap(),
    )


def _notify_strong_matches(
    candidates: Sequence[tuple[str, str, object]],
    results: Sequence[tuple[str, str, object]],
    *,
    min_score: int,
) -> None:
    """Emit a ``match`` notify for every applied verdict at/over ``min_score``.

    Mirrors the runner's §4.7 pipeline sink for store-side scoring paths (the daemon's
    match drain and ``rematch``): the fields come off the rebuilt row candidates rather
    than a live crawl. Best-effort by construction — ``notify.notify`` always logs, only
    POSTs when a webhook is configured, and never raises into the caller.
    """
    sent = {(site, url): enriched for site, url, enriched in candidates}
    for site, url, verdict in results:
        score = getattr(verdict, "score", 0)
        if score < min_score:
            continue
        enriched = sent.get((site, url))
        if enriched is None:  # defensive: every result came from candidates
            continue
        job = getattr(enriched, "job", None)
        if job is None:
            continue
        notify.notify(
            "match",
            f"{job.title} at {job.company} — "
            f"{getattr(verdict, 'verdict', '?')} ({score})",
            site=site,
            url=url,
            title=job.title,
            company=job.company,
            score=score,
            reasons="; ".join(getattr(verdict, "reasons", ())),
        )


def _match_drain_pass(
    store: StateStore, matcher_cell: dict[str, "Matcher | None"]
) -> dict[str, int]:
    """One §4.7 match-drain pass: profile watch, requeue on edit, store-side rescoring.

    Runs on a worker thread off the scheduler's drain timer. Three steps:

      1. **Profile watch.** Reload ``PROFILE.md`` from disk. A hash change against the
         matcher (or a matcher that never got built — profile/model arrived after boot)
         rebuilds the matcher in ``matcher_cell`` so crawls score under the new profile
         from this moment. A profile DELETED mid-run disables matching (cell → None); a
         momentarily malformed one (mid-edit save) keeps the old matcher and retries
         next pass — an edit in progress must not thrash the pipeline.
      2. **Requeue on edit.** When the persisted ``profile_hash`` meta differs from the
         disk profile, every open position is parked for rescoring. The meta survives
         restarts, so an edit made while the daemon was down is caught on the boot
         pass. First adoption of the meta key records the hash without a requeue.
      3. **Drain.** Parked rows are rebuilt from their stored descriptions
         (:func:`jobcrawler.match.drain_candidates`) and re-presented to the matcher —
         cache hits are free, the §8 daily cap bounds fresh scoring, strong verdicts
         notify exactly like the runner's sink.
    """
    from jobcrawler.match import drain_candidates  # lazy: pulls dspy

    try:
        profile = load_profile(config.profile_path())
    except ProfileNotFound as exc:
        if matcher_cell["m"] is not None:
            _log.warning("PROFILE.md gone — match scoring disabled: %s", exc)
            matcher_cell["m"] = None
        return {}
    except (ValueError, yaml.YAMLError) as exc:
        _log.warning("PROFILE.md malformed — keeping current profile: %s", exc)
        return {}

    matcher = matcher_cell["m"]
    if matcher is None or matcher._profile.profile_hash != profile.profile_hash:
        rebuilt = build_matcher(store)
        if rebuilt is None:
            return {}  # no model (or profile vanished between reads); warned inside
        matcher_cell["m"] = matcher = rebuilt

    requeued = 0
    last_hash = store.get_meta("profile_hash")
    if last_hash != profile.profile_hash:
        if last_hash is not None:
            requeued = store.requeue_open_matches()
            _log.info(
                "profile changed (%s -> %s): requeued %d open position(s)",
                last_hash[:12],
                profile.profile_hash[:12],
                requeued,
            )
        store.set_meta("profile_hash", profile.profile_hash)

    candidates, stats = drain_candidates(
        store, matcher._profile.prefs, limit=config.match_drain_cap()
    )
    summary: dict[str, int] = {"requeued": requeued, **stats}
    if candidates:
        result = matcher.score_new(candidates, drain=True)
        summary["cached"] = cast(int, result["cached"])
        summary["scored"] = cast(int, result["scored"])
        summary["pending"] = cast(int, result["pending"])
        _notify_strong_matches(
            candidates,
            cast("Sequence[tuple[str, str, object]]", result["results"]),
            min_score=config.match_min_score(),
        )
    return summary


def crawl_one(
    site: Site,
    *,
    store: StateStore,
    matcher: Matcher | None = None,
    crawlers_dir: Path = _CRAWLERS_DIR,
) -> CrawlSummary:
    """Run the committed crawler in a bounded subprocess, then upsert host-side (§4.4/§4.7).

    The committed script runs unattended on the production schedule, so it executes in
    the §4.4 child (AST gate + RLIMIT_AS / RLIMIT_CPU + wall-clock): a runaway crawler
    (infinite loop, memory blowup) is bounded and reported as a failed run, never
    allowed to wedge or OOM the daemon and take the whole fleet down with it. The child
    only fetches + yields ``Job``s; the host keeps dedup, the completeness filter, the
    §4.7 upsert/closure, and — when ``matcher`` is given — the §6 prefilter + LLM match.

    ``matcher`` is the shared §4.7 profile matcher; the caller threads ONE instance
    through every site so the §8 daily match cap and the match rate-limit hold globally
    across concurrent crawls (a per-call matcher would multiply both by the fan-out).
    ``None`` is crawl-only mode (no ``PROFILE.md``).
    """
    source = _crawler_file(site.slug, crawlers_dir).read_text()
    return crawl_site_isolated(
        site.slug,
        source,
        allowed_hosts_of(source),  # SITE + declared EXTRA_HOSTS (§8)
        store,
        matcher=matcher,
        timeout=config.crawl_timeout(),  # wall-clock budget (§4.4)
        mem_mb=config.crawl_mem_mb(),  # RLIMIT_AS
        cpu_seconds=config.crawl_cpu_seconds(),  # RLIMIT_CPU (decoupled from wall clock)
        first_crawl_cap=config.first_crawl_cap(),  # newest-K on a cold start (§4.7)
        min_description_chars=config.min_description_chars(),  # completeness floor (§4.6)
        expired_markers=config.expired_markers(),  # drop postings flagged expired (§4.7)
        close_after_misses=config.close_after_misses(),  # closure streak (§4.7)
        enumerates_all=getattr(
            site, "enumerates_all", True
        ),  # mass-closure guard (§4.7)
        expected_positions=getattr(
            site, "expected_positions", 0
        ),  # enumeration floor (§4.7)
        match_min_score=config.match_min_score(),  # notify threshold (§4.7)
    )


# -- startup preflight -------------------------------------------------------


class PreflightError(Exception):
    """A run/crawl startup precondition (config / registry / profile / state) failed (§4.9).

    Raised with an operator-actionable message so a misconfiguration fails fast at
    boot instead of surfacing as an opaque error deep in the run loop.
    """


def _preflight_auth() -> None:
    """Fail fast if a daemon LM surface uses a ``chatgpt/*`` slug with no usable token (§9).

    ``run``/``crawl`` bill LM tokens only through the §4.7 matcher, and ``run``'s §4.8
    self-heal additionally drives the generator (root + ``sub_lm``); any of those slugs may
    be ``chatgpt/*`` (ChatGPT-subscription OAuth). All ``chatgpt/*`` slugs share one OAuth
    token store, so this validates it once per distinct configured slug —
    :func:`jobcrawler.auth.ensure_logged_in` imports from ``codex login`` if jobcrawler's own
    store is empty and refreshes non-interactively — raising :class:`PreflightError` (never
    blocking on litellm's device-code prompt) when no valid credential is reachable.

    Imported lazily and gated on :func:`jobcrawler.lm.is_chatgpt` (which pulls no heavy deps)
    so a plain OpenRouter deployment pays nothing here.
    """
    from jobcrawler.lm import is_chatgpt

    slugs = {
        s
        for s in (config.model(), config.sub_model(), config.match_model())
        if s and is_chatgpt(s)
    }
    if not slugs:
        return
    from jobcrawler.auth import AuthError, ensure_logged_in

    for slug in slugs:
        try:
            ensure_logged_in(slug)
        except AuthError as exc:
            raise PreflightError(str(exc)) from exc


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
    profile_path: str | None = None,
) -> list[Site]:
    """Validate run/crawl startup preconditions, returning the loaded registry (§4.9).

    Fails fast (``PreflightError`` with an actionable message) on a missing or malformed
    ``sites.yaml``, an invalid site entry, a **malformed** ``PROFILE.md``, or an
    unwritable state path — the opaque mid-run failures these would otherwise become. An
    empty *enabled* registry is a warning, not fatal (an operator may be staging the
    list); the daemon would simply idle. A **missing** ``PROFILE.md`` is likewise only a
    warning — running with no profile is a valid crawl-only mode (positions are still
    tracked, just not match-scored), so its absence must never block a crawl.
    """
    sites_path = sites_path or config.sites_path()
    db_path = db_path or _db_path()
    profile_path = profile_path or config.profile_path()

    try:
        sites = load_sites(sites_path)
    except FileNotFoundError as exc:
        raise PreflightError(
            f"site registry not found at {sites_path} "
            "(set JOBCRAWLER_SITES or create the file)"
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

    # Profile: a MISSING PROFILE.md is a valid crawl-only mode (warn and continue); a
    # MALFORMED one — bad prefs enum, unclosed frontmatter, non-mapping YAML — is a loud
    # fail-fast, since the operator authored broken preferences and expects matching.
    try:
        profile = load_profile(profile_path)
    except ProfileNotFound:
        _log.warning(
            "preflight: no profile at %s — crawling without match scoring "
            "(create PROFILE.md to enable §4.7 matching)",
            profile_path,
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise PreflightError(f"profile {profile_path} is malformed: {exc}") from exc
    else:
        _log.info(
            "preflight: profile ok (hash %s, %d discipline(s), %d level(s))",
            profile.profile_hash[:12],
            len(profile.prefs.disciplines),
            len(profile.prefs.levels),
        )

    reason = _state_writability(db_path)
    if reason is not None:
        raise PreflightError(f"state store is not writable: {reason}")

    # A chatgpt/* model slug bills against a ChatGPT subscription via an OAuth token, not
    # an API key — fail fast (with a `codex login` / `jobcrawl login` hint) if it's absent,
    # rather than blocking the daemon on litellm's interactive device-code prompt (§9).
    _preflight_auth()

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
    # distribution, drift-recovery MTTR, the §4.7 position lifecycle + §4.7 match spend,
    # and the §4.11 self-improvement state. Pure read; opens the store, renders, closes.
    store = StateStore(_db_path())
    try:
        for line in render_status(load_sites(config.sites_path()), store):
            print(line)
    finally:
        store.close()
    return 0


def cmd_positions(args: argparse.Namespace) -> int:
    # List positions with aligned columns (score/title/company/location/workplace/age),
    # ordered by §4.7 match score desc by default. Routes through search_positions so the
    # sort and the open/closed/all lifecycle filter are resolved in SQL: sorting or
    # min-score filtering in Python *after* a LIMIT would only touch a truncated window.
    store = StateStore(_db_path())
    try:
        status = None if args.status == "all" else args.status
        rows = store.search_positions(
            site=args.site,
            status=status,
            min_score=args.min_score,
            sort=args.sort,
            dismissed=None,
            limit=args.limit,
        )
        if not rows:
            print("no positions")
        for line in render_positions(rows):
            print(line)
    finally:
        store.close()
    return 0


def cmd_matches(args: argparse.Namespace) -> int:
    # Top open positions by §4.7 match score (desc), with the LLM's reasons. --min
    # defaults to config.match_min_score() (the notify threshold), so `matches` shows
    # exactly what would ping an operator.
    store = StateStore(_db_path())
    try:
        min_score = args.min if args.min is not None else config.match_min_score()
        rows = store.matches(min_score, limit=args.limit)
        if not rows:
            print(f"no open positions scored >= {min_score}")
        for line in render_matches(rows):
            print(line)
    finally:
        store.close()
    return 0


def _parse_cached_verdict(cached: str | None) -> tuple[int, str, str, str] | None:
    """Decode a cached verdict JSON into ``(score, verdict, reasons_json, concerns_json)``.

    Mirrors the match cache's stored shape (``{score, verdict, reasons, concerns}``) so
    a cache-drain can apply a verdict without re-billing the LLM. Concerns ride along so a
    cache-replayed row is as trainset-complete as a freshly-scored one (§8). A corrupt or
    absent entry is a miss (``None``) — the cache is an optimization, never a correctness
    dependency.
    """
    if not cached:
        return None
    try:
        data = json.loads(cached)
        score = max(0, min(100, int(data["score"])))
    except (ValueError, TypeError, KeyError):
        return None
    verdict = str(data.get("verdict") or "")
    reasons = json.dumps(list(data.get("reasons") or []))
    concerns = json.dumps(list(data.get("concerns") or []))
    return score, verdict, reasons, concerns


def cmd_rematch(args: argparse.Namespace) -> int:
    store = StateStore(_db_path())
    try:
        return _run_rematch(store)
    finally:
        store.close()


def _run_rematch(store: StateStore) -> int:
    """Re-score the tracked backlog against the CURRENT profile, from the store (§4.7).

    Three passes. (1) and (2) are content-addressed cache lookups keyed by the current
    ``profile_hash`` — free: drain parked rows whose verdict is already cached, and
    re-apply / invalidate the open scored rows (a verdict cached under an old profile
    misses and parks the row). (3) then LLM-scores what remains parked, rebuilt straight
    from the rows' stored descriptions — a profile edit re-scores NOW instead of waiting
    for every board's next crawl; the §8 daily cap and drain cap still bound one
    invocation's spend, and the daemon's drain timer works through any remainder.
    Reports scored (from cache), rescored (updated verdicts), llm_scored (fresh),
    pending (still parked), cache_hits.
    """
    from jobcrawler.match import PROMPT_VERSION, drain_candidates  # lazy: pulls dspy

    path = config.profile_path()
    try:
        profile = load_profile(path)
    except ProfileNotFound as exc:
        _log.error("rematch needs a profile: %s", exc)
        return 2
    except (ValueError, yaml.YAMLError) as exc:
        _log.error("rematch: malformed profile at %s: %s", path, exc)
        return 2
    try:
        model = config.require_match_model()
    except RuntimeError as exc:
        _log.error("rematch: %s", exc)
        return 2

    profile_hash = profile.profile_hash
    scored = cache_hits = still_pending = rescored = invalidated = 0

    # 1. Drain the pending backlog against the current profile's cache (free hits).
    for rec in store.pending_matches(limit=config.recheck_cap()):
        parsed = _parse_cached_verdict(
            store.cached_match(rec.content_hash, profile_hash, model, PROMPT_VERSION)
        )
        if parsed is None:
            still_pending += 1
            continue
        score, verdict, reasons, concerns = parsed
        store.set_match(
            rec.site,
            rec.url,
            score=score,
            verdict=verdict,
            reasons=reasons,
            concerns=concerns,
        )
        scored += 1
        cache_hits += 1

    # 2. Re-evaluate already-scored open positions against the current profile_hash. A
    #    current-profile cache hit that differs is applied for free; a miss means the
    #    stored verdict predates the profile edit → park it pending for the next crawl.
    for rec in store.open_positions(limit=10_000):
        if rec.match_score is None:
            continue
        parsed = _parse_cached_verdict(
            store.cached_match(rec.content_hash, profile_hash, model, PROMPT_VERSION)
        )
        if parsed is None:
            store.set_match_pending(rec.site, rec.url)
            invalidated += 1
            continue
        score, verdict, reasons, concerns = parsed
        if score != rec.match_score or verdict != (rec.match_verdict or ""):
            store.set_match(
                rec.site,
                rec.url,
                score=score,
                verdict=verdict,
                reasons=reasons,
                concerns=concerns,
            )
            rescored += 1
            cache_hits += 1

    # 3. LLM-score what stayed parked, rebuilt from the stored rows. Prefilter misses
    #    are unparked verdict-less inside drain_candidates; rows without a stored
    #    description (pre-persistence writes) stay parked for their next crawl to
    #    backfill. The persisted profile_hash meta is refreshed so the daemon's drain
    #    tick doesn't requeue the backlog a second time for the same edit.
    llm_scored = 0
    matcher = build_matcher(store)
    if matcher is not None:
        candidates, _stats = drain_candidates(
            store, profile.prefs, limit=config.match_drain_cap()
        )
        if candidates:
            result = matcher.score_new(candidates, drain=True)
            llm_scored = cast(int, result["cached"]) + cast(int, result["scored"])
            _notify_strong_matches(
                candidates,
                cast("Sequence[tuple[str, str, object]]", result["results"]),
                min_score=config.match_min_score(),
            )
    store.set_meta("profile_hash", profile_hash)

    pending_now = store.position_counts()["pending"]
    print(
        f"rematch: scored={scored} rescored={rescored} llm_scored={llm_scored} "
        f"pending={pending_now} cache_hits={cache_hits}"
    )
    return 0


def _fmt_salary_floor(floor: object) -> str:
    """Render a :class:`~jobcrawler.profile.SalaryFloor` (or None) for the profile view."""
    if floor is None:
        return "(none)"
    return (
        f"{getattr(floor, 'amount', '?')} {getattr(floor, 'currency', '')}"
        f"/{getattr(floor, 'period', '')}"
    )


def cmd_profile(args: argparse.Namespace) -> int:
    # Parse and pretty-print the active PROFILE.md: the prefilter prefs, the content
    # hash (which keys the match cache — a change invalidates verdicts), and the LLM
    # body length. A missing profile is a soft miss (exit 1, nothing to show); a
    # MALFORMED one fails loud (exit 2) naming the offending value.
    path = config.profile_path()
    try:
        profile = load_profile(path)
    except ProfileNotFound as exc:
        print(f"no active profile: {exc}")
        return 1
    except (ValueError, yaml.YAMLError) as exc:
        print(f"malformed profile at {path}: {exc}")
        return 2
    prefs = profile.prefs
    print(f"profile: {path}")
    print(f"  hash:              {profile.profile_hash}")
    print(f"  disciplines:       {', '.join(sorted(prefs.disciplines)) or '(any)'}")
    print(f"  levels:            {', '.join(sorted(prefs.levels)) or '(any)'}")
    print(f"  workplace:         {', '.join(sorted(prefs.workplace)) or '(any)'}")
    print(f"  locations:         {', '.join(prefs.locations) or '(any)'}")
    print(f"  languages:         {', '.join(prefs.languages) or '(any)'}")
    print(f"  min_salary:        {_fmt_salary_floor(prefs.min_salary)}")
    print(f"  exclude_keywords:  {', '.join(prefs.exclude_keywords) or '(none)'}")
    print(f"  exclude_companies: {', '.join(prefs.exclude_companies) or '(none)'}")
    print(f"  body:              {len(profile.body)} chars")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_one(args.slug)
    if result.exception is not None:
        print(f"FAIL {args.slug}: {result.exception.type}: {result.exception.message}")
        return 1
    print(
        f"OK {args.slug}: {result.item_count} positions, "
        f"{len(result.schema_errors)} schema errors (no upsert)"
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
    # One shared matcher for the batch: the §8 daily cap + match rate-limit are global,
    # so every site scores through the same instance (§4.7). None → crawl-only.
    matcher = build_matcher(store)
    try:
        for site in _crawl_targets(args):
            summary = crawl_one(site, store=store, matcher=matcher)
            print(
                f"{summary.site}: found={summary.positions_found} "
                f"new={summary.new_positions} changed={summary.changed_positions} "
                f"closed={summary.closed_positions} matched={summary.matched} "
                f"pending={summary.match_pending} incomplete={summary.incomplete} "
                f"errors={summary.errors}"
            )
    finally:
        store.close()
    return 0


def _probe(slug: str, *, crawlers_dir: Path = _CRAWLERS_DIR) -> int:
    """Re-run a committed crawler in the bounded subprocess, counting complete jobs (§4.8).

    The §4.8 re-probe the self-healer uses to tell a real failure from a transient
    outage. It runs through the same §4.4 isolation as the live crawl — a drifted
    crawler can hang on the reprobe too, and that hang must be bounded, not block the
    heal loop — with no upsert and no state. A site whose committed script is missing
    or fails the gate probes to 0 (a confirmed failure → regenerate), never an
    exception that aborts the loop.
    """
    try:
        source = _crawler_file(slug, crawlers_dir).read_text()
    except OSError:
        return 0  # no committed script → confirmed failure, not a transient blip
    return probe_isolated(
        source,
        allowed_hosts_of(source),  # SITE + declared EXTRA_HOSTS (§8)
        min_description_chars=config.min_description_chars(),
        expired_markers=config.expired_markers(),  # count only live (§4.7) postings
        timeout=config.crawl_timeout(),
        mem_mb=config.crawl_mem_mb(),
        cpu_seconds=config.crawl_cpu_seconds(),
    )


def _record_site_failure(sites: Sequence[Site], slug: str) -> None:
    """Capture a confirmed-failed site as a §4.11 hard case (the optimizer's signal).

    Only the site identity / render / hints are stored; the corpus is re-prefetched
    fresh at optimize time so the strategy is scored against the site's *current*
    (drifted) state (:func:`jobcrawler.trainset.record_failure`).
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


def _make_sites_reloader(path: str) -> Callable[[], list[Site] | None]:
    """A §4.1 hot-reload probe for the scheduler: return the freshly parsed registry
    when ``sites.yaml`` changed on disk since the last call, else ``None``.

    Lets an operator add or retire boards without bouncing the daemon. The probe is
    cheap on the common path — a single ``stat`` — and only re-parses when the mtime
    moves. A malformed mid-edit save (a half-written file caught between truncate and
    write) is swallowed with a warning and the current roster is kept; the next clean
    save bumps the mtime and is picked up. The mtime advances even past a failed parse
    so a persistently-broken file warns once, not every tick.
    """
    try:
        last_mtime: float | None = os.stat(path).st_mtime
    except OSError:
        last_mtime = None  # not there yet; a later create will register as a change

    def reload() -> list[Site] | None:
        nonlocal last_mtime
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return None  # briefly absent (atomic replace in flight) — retry next tick
        if mtime == last_mtime:
            return None
        last_mtime = mtime
        try:
            return load_sites(path)
        except (yaml.YAMLError, ValidationError, OSError) as exc:
            _log.warning(
                "sites.yaml changed but did not parse (%s); keeping the current roster",
                exc,
            )
            return None

    return reload


def _build_healer(
    store: StateStore,
    sites: Sequence[Site],
    *,
    sites_source: Callable[[], Sequence[Site]] | None = None,
) -> SelfHealer:
    """The production §4.8 self-healer the scheduler drives on its regen timer.

    ``reprobe`` re-runs the live crawler (a real failure vs a transient blip);
    ``regenerate`` re-runs the generator for the slug (lazy import — it pulls dspy).
    A confirmed failure is first captured as a §4.11 hard case, so a genuinely
    broken site feeds the offline optimizer before self-healing tries to fix it.
    Both live calls run off the event loop via ``to_thread``.

    ``sites_source`` (when given) is consulted at failure time for the *current*
    registry, so a board added or retired since boot via §4.1 hot-reload is resolved
    against the live roster rather than the frozen one; it falls back to ``sites``.
    """
    resolve_sites = sites_source if sites_source is not None else lambda: sites

    async def reprobe(slug: str) -> int:
        return await asyncio.to_thread(_probe, slug)

    async def regenerate(slug: str) -> RegenOutcome:
        from jobcrawler.cli_generate import regenerate_one

        # regenerate_one honors the tri-state JOBCRAWLER_REVIEW_GATE: `on` (default)
        # stages the crawler (RegenOutcome.STAGED → pending-review, §14.2); `auto`
        # backs up + promotes it live (reversible); `off` writes live with no backup.
        return await asyncio.to_thread(regenerate_one, slug)

    async def promote(slug: str) -> bool:
        # Auto-clear a pending-review strand (§4.8): move the staged crawler live when
        # the gate is no longer `on`. Pure filesystem, run off the loop like the rest.
        from jobcrawler.cli_generate import promote_one

        ok, _ = await asyncio.to_thread(promote_one, slug)
        return ok

    return SelfHealer(
        store,
        reprobe=reprobe,
        regenerate=regenerate,
        on_confirmed_failure=lambda slug: _record_site_failure(resolve_sites(), slug),
        body_quality_floor=config.description_quality_floor(),
        genfail_backoff=config.genfail_backoff(),
        genfail_backoff_cap=config.genfail_backoff_cap(),
        genfail_max_attempts=config.genfail_max_attempts(),
        review_mode=config.review_gate,
        promote=promote,
        has_crawler=_has_live_crawler,  # §4.2 cold-start: write a first crawler if none
    )


def cmd_run(args: argparse.Namespace) -> int:
    _configure_logging()  # the daemon must not run silent (§4.9)
    sites, code = _preflight_or_exit()  # fail fast on bad config, never mid-loop
    if sites is None:
        return code

    # Single-instance guard (before reap_orphans, which assumes it is the only
    # process): a second concurrent daemon on the same store would reap this one's
    # in-flight runs and double-dispatch every site. Refuse rather than race.
    lock_fd = _acquire_run_lock()
    if lock_fd is None:
        _log.error(
            "another `jobcrawl run` daemon already holds %s — refusing to start a "
            "second scheduler on the same store",
            config.run_lock_path(),
        )
        return 1

    store = StateStore(_db_path())

    # A prior daemon killed mid-crawl leaves finished=NULL run rows; the scheduler
    # would treat those sites as perpetually in-flight and never re-dispatch them.
    # Reap before the scheduler starts (single-process §4.9: nothing is in-flight yet).
    reaped = store.reap_orphans()
    if reaped:
        _log.warning("reaped %d orphaned run row(s) from a prior crash", reaped)

    # One shared matcher across all concurrent crawls so the §8 daily match cap and the
    # match rate-limit hold globally: the scheduler runs up to `concurrency` crawls at
    # once on to_thread workers, and a per-crawl matcher would let the token budget be
    # exceeded by the fan-out factor. None → crawl-only mode (no PROFILE.md). Held in a
    # one-slot cell (not a plain local) so the §4.7 match-drain pass can swap in a
    # rebuilt matcher when PROFILE.md changes — crawls read the cell at dispatch time
    # and score under the new profile without a daemon restart.
    matcher_cell: dict[str, Matcher | None] = {"m": build_matcher(store)}

    async def crawl(site: Site) -> CrawlSummary:
        return await asyncio.to_thread(
            crawl_one, site, store=store, matcher=matcher_cell["m"]
        )

    async def match_drain() -> dict[str, int]:
        return await asyncio.to_thread(_match_drain_pass, store, matcher_cell)

    # §4.1 hot-reload: the scheduler re-stats sites.yaml each tick and swaps in an
    # added/retired board without a restart; the healer's failure-capture hook reads the
    # same file fresh so a just-added board that breaks still feeds the §4.11 trainset.
    def _live_sites() -> Sequence[Site]:
        try:
            return load_sites(config.sites_path())
        except (FileNotFoundError, yaml.YAMLError, ValidationError, OSError):
            return sites  # unreadable mid-edit; fall back to the boot roster

    scheduler = Scheduler(
        sites,
        store=store,
        crawl=crawl,
        healer=_build_healer(store, sites, sites_source=_live_sites),
        has_crawler=_has_live_crawler,  # route crawler-less sites to §4.2 bootstrap
        reload_sites=_make_sites_reloader(config.sites_path()),  # §4.1 hot-reload
        match_drain=match_drain,  # §4.7 profile watch + store-side pending drain
        # §4.9 recheck: without this seam recheck_due() is always False, so neither the
        # windowed 404/expired-marker re-verification NOR the fleet-wide expire_due()
        # sweep (which lives inside recheck_tick) ever runs — stale/closed positions
        # would never close. A host-side polite GET (per-host paced + robots, no page
        # budget) mirrors the crawl fetch path so the sweep activates in production.
        recheck_fetch=build_recheck_fetch(),
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
        os.close(lock_fd)  # release the single-instance lock (also freed on crash)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    _configure_logging()  # the web server must not run silent either (§4.9)
    # The §16 tracker UI is a second long-running process over the SAME store the daemon
    # owns; WAL mode (§4.7) makes the shared file safe for concurrent readers + this
    # writer. FastAPI/uvicorn/jinja2 are heavy and only this command needs them, so import
    # lazily — the rest of the CLI must not pay startup cost for a UI it never launches.
    # serve_app resolves host/port (flag > env > config default), opens the store, loads
    # the site registry best-effort, and blocks in uvicorn until SIGINT/SIGTERM.
    from jobcrawler.web import serve_app

    return serve_app(host=args.host, port=args.port)


def cmd_generate(args: argparse.Namespace) -> int:
    # The generator needs a configured LM + Deno sandbox + live fetch; its deps
    # are heavy, so import lazily and let the runtime surface any missing setup.
    from jobcrawler.cli_generate import generate_command

    return generate_command(args)


def cmd_promote(args: argparse.Namespace) -> int:
    # Promote a self-heal candidate staged under crawlers/_pending (§14.2) to live.
    # promote_one is pure filesystem, but it lives beside generate (shared crawlers/
    # paths), so import it lazily to keep cli.py free of the dspy-heavy module at start.
    from jobcrawler.cli_generate import promote_one

    ok, message = promote_one(args.slug)
    print(message)
    return 0 if ok else 1


def cmd_rollback(args: argparse.Namespace) -> int:
    # Restore a site's prior crawler from its crawlers/_history backup — the undo for
    # an `auto` self-heal promotion (§4.8). Pure filesystem, but it shares crawlers/
    # paths with generate, so import it lazily to keep cli.py free of dspy at start.
    from jobcrawler.cli_generate import rollback_one

    ok, message = rollback_one(args.slug)
    print(message)
    return 0 if ok else 1


def cmd_optimize(args: argparse.Namespace) -> int:
    # The offline §4.11 self-improvement job (build trainset → optimize → promote);
    # its deps (dspy + the sandbox) are heavy, so import lazily.
    from jobcrawler.cli_optimize import optimize_command

    return optimize_command(args)


def cmd_match_trainset(args: argparse.Namespace) -> int:
    # Project persisted match verdicts + operator feedback into a DSPy matcher trainset
    # (§8) — the labelled data for later optimising a cheaper matcher. dspy is heavy, so
    # the projector is imported lazily.
    from jobcrawler import match_trainset

    out_path = args.out or "match-trainset.jsonl"
    try:
        profile = load_profile(config.profile_path())
    except ProfileNotFound:
        print(f"no profile at {config.profile_path()} — nothing to label against")
        return 1
    store = StateStore(_db_path())
    try:
        # Every scored row across the whole lifecycle, dismissed included — a dismissed or
        # closed-but-applied posting is exactly the operator-corrected label we want.
        rows = store.search_positions(
            min_score=0, status=None, dismissed=None, sort="score", limit=1_000_000
        )
        examples = match_trainset.build_examples(rows, profile.body)
        match_trainset.save_trainset(examples, out_path)
    finally:
        store.close()
    print(f"wrote {len(examples)} matcher example(s) to {out_path}")
    return 0


def cmd_rollback_generator(args: argparse.Namespace) -> int:
    # Undo the last generator adoption (the §4.11 versioned rollback). Flips the
    # active-generator pointer back to the prior version (or baseline); cheap, no LM.
    from jobcrawler import generator_pointer

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


def cmd_login(args: argparse.Namespace) -> int:
    # Import the ChatGPT-subscription (Codex) OAuth token from the official `codex login`
    # (~/.codex/auth.json) into jobcrawler's token store so a `chatgpt/*` model slug can
    # bill against the subscription (§9). One-shot, no preflight; lazy import pulls litellm.
    from jobcrawler.auth import AuthError, login

    try:
        info = login()
    except AuthError as exc:
        print(f"login failed: {exc}")
        return 2
    account = info["account_id"] or "unknown account"
    print(
        f"logged in to ChatGPT (account {account}); token stored under {info['auth_dir']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobcrawl", description="RLM job tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="start the scheduler daemon").set_defaults(func=cmd_run)

    srv = sub.add_parser("serve", help="start the web tracker UI (§16)")
    srv.add_argument(
        "--host", help="bind address (default: JOBCRAWLER_SERVE_HOST or 127.0.0.1)"
    )
    srv.add_argument(
        "--port", type=int, help="listen port (default: JOBCRAWLER_SERVE_PORT or 8765)"
    )
    srv.set_defaults(func=cmd_serve)

    gen = sub.add_parser("generate", help="run RLM, gate, write crawlers/<slug>.py")
    target = gen.add_mutually_exclusive_group(required=True)
    target.add_argument("slug", nargs="?")
    target.add_argument("--all", action="store_true")
    target.add_argument(
        "--missing",
        action="store_true",
        help="every enabled site with no committed crawler yet (drain the §4.2 backlog)",
    )
    target.add_argument("--stale", action="store_true")
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate", help="dry-run a script, no upsert")
    val.add_argument("slug")
    val.set_defaults(func=cmd_validate)

    crawl = sub.add_parser("crawl", help="run committed scripts, dedup, upsert, match")
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
        "status",
        help="per-site run aggregates, fleet health, drift MTTR, positions/matches (M4)",
    ).set_defaults(func=cmd_status)

    pos = sub.add_parser(
        "positions", help="list open positions (score/title/company/…)"
    )
    pos.add_argument("--site", help="restrict to one site slug")
    pos.add_argument(
        "--status",
        choices=["open", "closed", "all"],
        default="open",
        help="lifecycle filter (default: open)",
    )
    pos.add_argument(
        "--min-score",
        type=int,
        dest="min_score",
        help="keep only positions scored at or above this §4.7 match score",
    )
    pos.add_argument(
        "--sort",
        choices=["score", "recent", "salary", "title", "updated"],
        default="score",
        help="ordering (default: best §4.7 match score, desc)",
    )
    pos.add_argument("--limit", type=int, default=50)
    pos.set_defaults(func=cmd_positions)

    mat = sub.add_parser(
        "matches", help="top open positions by §4.7 match score, with reasons"
    )
    mat.add_argument(
        "--min", type=int, help="minimum match score (default: config.match_min_score)"
    )
    mat.add_argument("--limit", type=int, default=20)
    mat.set_defaults(func=cmd_matches)

    rem = sub.add_parser(
        "rematch",
        help="drain the pending-match backlog + re-evaluate verdicts after a profile edit",
    )
    rem.set_defaults(func=cmd_rematch)

    prof = sub.add_parser(
        "profile", help="show the active candidate profile (prefs + hash)"
    )
    prof.set_defaults(func=cmd_profile)

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

    mt = sub.add_parser(
        "match-trainset",
        help="project persisted match verdicts + feedback into a DSPy matcher trainset (§8)",
    )
    mt.add_argument(
        "--out",
        help="output JSONL path (default: match-trainset.jsonl)",
    )
    mt.set_defaults(func=cmd_match_trainset)

    sub.add_parser(
        "rollback-generator",
        help="revert the active generator to the previously-adopted version (§4.11)",
    ).set_defaults(func=cmd_rollback_generator)

    sub.add_parser(
        "login",
        help="import the ChatGPT-subscription (Codex) OAuth token from `codex login` (§9)",
    ).set_defaults(func=cmd_login)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
