"""The crawl runner (§4.7): crawl → track → match, the pipeline heart.

One runner executes any committed crawler. It calls ``crawl(ctx)``, then owns
every host-side policy the (dumb) state store does not: validation, dedup,
lifecycle, and the single new LLM surface — profile matching. The story for one
crawl, in order:

  1. **crawl → validate (§4.6).** Drain ``crawl(ctx)`` into a list of :class:`Job`,
     isolating any failure to *this* site (§4.4): a script that raises, yields a
     non-``Job``, or finds nothing is a health signal, never a fleet-aborting crash.
  2. **completeness + expired floor (§4.6/§4.7).** A description below
     ``min_description_chars`` is a stub/truncated extraction — dropped, left
     untracked (a later full fetch upserts it fresh), counted ``incomplete``. A
     description carrying an ``expired_markers`` phrase ("position filled") is a dead
     posting — :meth:`~StateStore.mark_closed` closes any open row and it is never
     tracked or matched. Both floors are host-side guarantees, independent of what
     the untrusted generated crawler chooses to yield; opt-in (default off) and wired
     to ``config`` by the CLI, so the runner's other callers keep prior behaviour.
  3. **content-hash → lifecycle upsert (§4.7).** Each surviving job is enriched
     (§4.6, deterministic — the derived discipline/level/workplace/salary populate the
     ``positions`` row) and upserted under a ``content_hash`` that MUST mirror the
     matcher's (:func:`jobcrawler.match._content_hash`) so a job's cache key is stable
     across both. ``upsert_position`` returns new/changed/unchanged; only new/changed
     carry forward to matching (an unchanged posting re-scores from cache for free).
  4. **prefilter → batched cached matching → notify (§6/§8).** When a matcher is
     wired, the new/changed jobs are run through the deterministic :func:`prefilter`
     (drops definite mismatches before a token is spent) and handed to
     :meth:`Matcher.score_new` (cached, batched, daily-cap-bounded, fail-open). Strong
     verdicts (``score >= match_min_score``) are emitted as ``match`` notify events.
     The matcher is **Optional**: ``None`` (no profile / autonomy off) means crawl +
     track WITHOUT matching — never a crash. The crawl/dedup/closure path is wholly
     independent of matcher health.
  5. **closure diff (§4.7) — guarded.** ``close_missing`` ages out open positions the
     crawl did not re-see, but ONLY on a *healthy full-enumeration* run: the source
     enumerates its whole board (``enumerates_all``), the crawl raised no top-level
     exception, and at least one position was actually tracked this crawl. This
     **mass-closure guard is a correctness invariant** — a broken crawl that yields 0
     (or whose descriptions all fail the floor) must never close the book on a site's
     positions. Windowed sources (``enumerates_all=False``, e.g. a "latest 20" feed)
     never diff-close here; a §4.9 per-URL recheck verifies their closures instead.

On a site's *first* position-yielding crawl an optional ``first_crawl_cap`` bounds only
the MATCHING load, never the tracking: every job is tracked, but only the freshest K
go to the matcher now (by ``posted_at``, discovery-order fallback) and the tail is
parked ``match_pending`` — so a large standing back-catalogue can't blow the daily LLM
budget on day one; the backlog drains over later runs.

Committed production crawls run through :func:`crawl_site_isolated` — the script
executes in the §4.4 child (AST gate + RLIMIT_AS/RLIMIT_CPU + wall-clock), so a runaway
crawler is bounded and reported, never wedging or OOMing the daemon; the child only
fetches + yields, while the host keeps ownership of dedup, the floors, matching, and the
run record. No LLM ever runs in the crawl loop (§4.6/§8): committed crawlers are plain
Python, and matching is host-side, outside the sandbox.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import timezone
from typing import cast

from jobcrawler import config, notify
from jobcrawler.match import Matcher, MatchVerdict, prefilter
from jobcrawler.runtime.contract import CrawlFn, Job
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.hardening import harden_xml
from jobcrawler.runtime.normalize import (
    EnrichedJob,
    canonical_company,
    enrich,
    normalize_location,
)
from jobcrawler.runtime.sandbox import collect_crawler
from jobcrawler.state import StateStore
from jobcrawler.urls import canonicalize_url


@dataclass(frozen=True, slots=True)
class CrawlSummary:
    """The §5 Run record counters plus the §4.7 lifecycle deltas for one crawl.

    ``positions_found`` is every valid ``Job`` yielded (before the completeness/expiry
    floors) — the blackout denominator (``== 0`` is a dead crawl) and the
    ``description_quality`` denominator. ``incomplete`` is how many were dropped by the
    completeness floor. ``new/changed/closed_positions`` are the lifecycle diffs; the
    diff-based ``closed_positions`` counts only the guarded ``close_missing`` result
    (an expired-marker force-close is a separate, immediate transition). ``matched`` is
    how many positions received a verdict this crawl (cache hits + fresh scores),
    ``match_pending`` how many were parked for a later run (first-crawl cap + daily-cap
    overflow + match failures). Both stay 0 when no matcher is wired.
    """

    site: str
    positions_found: int
    new_positions: int
    changed_positions: int
    closed_positions: int
    errors: int
    pages_fetched: int
    incomplete: int = 0
    matched: int = 0
    match_pending: int = 0


def crawl_site(
    slug: str,
    crawl_fn: CrawlFn,
    ctx: Ctx,
    store: StateStore,
    *,
    matcher: Matcher | None = None,
    enumerates_all: bool = True,
    expected_positions: int = 0,
    first_crawl_cap: int | None = None,
    min_description_chars: int = 0,
    expired_markers: tuple[str, ...] = (),
    close_after_misses: int = 2,
    match_min_score: int = 70,
) -> CrawlSummary:
    """In-process crawl: crawl → track → match. The test/reference surface.

    Drives the real pipeline while the caller holds the ``crawl_fn`` + ``ctx`` itself.
    Production runs *committed* scripts through :func:`crawl_site_isolated` instead
    (subprocess-bounded, §4.4); this in-process form trusts its caller to have already
    bounded the crawler.
    """
    harden_xml()  # XXE / billion-laughs off before the crawler parses feeds (§8)
    is_first = store.last_run(slug) is None
    run_id = store.start_run(slug)
    jobs, errors, crawl_raised = _collect(crawl_fn, ctx)
    return _process(
        slug,
        run_id,
        is_first,
        jobs,
        errors,
        crawl_raised,
        ctx.pages_fetched,
        store,
        matcher=matcher,
        enumerates_all=enumerates_all,
        expected_positions=expected_positions,
        first_crawl_cap=first_crawl_cap,
        min_description_chars=min_description_chars,
        expired_markers=expired_markers,
        close_after_misses=close_after_misses,
        match_min_score=match_min_score,
    )


def crawl_site_isolated(
    slug: str,
    source: str,
    allowed_hosts: Collection[str],
    store: StateStore,
    *,
    timeout: float = 600.0,
    mem_mb: int = 1024,
    cpu_seconds: float = 120.0,
    matcher: Matcher | None = None,
    enumerates_all: bool = True,
    expected_positions: int = 0,
    first_crawl_cap: int | None = None,
    min_description_chars: int = 0,
    expired_markers: tuple[str, ...] = (),
    close_after_misses: int = 2,
    match_min_score: int = 70,
) -> CrawlSummary:
    """Run a *committed* crawler in a bounded subprocess, then track + match host-side.

    The production crawl path. The script executes in the §4.4 child (AST gate +
    RLIMIT_AS / RLIMIT_CPU + wall-clock), so a runaway crawler (infinite loop, memory
    blowup) is bounded and reported rather than wedging or OOMing the daemon. The child
    only fetches + yields jobs; the host keeps ownership of dedup, the floors, the
    lifecycle, matching, and the run record — the child never touches the matcher or the
    state store. A hang / crash / gate rejection comes back as a failed run (0 positions,
    >=1 error, ``crawl_raised``), never a raise (§4.4 failure isolation) — and because
    the enumeration was cut short, closure is skipped for that run (the mass-closure
    guard). XML hardening runs inside the child, so it is not repeated here.
    """
    is_first = store.last_run(slug) is None
    run_id = store.start_run(slug)
    result = collect_crawler(
        source,
        allowed_hosts=allowed_hosts,
        timeout=timeout,
        mem_mb=mem_mb,
        cpu_seconds=cpu_seconds,
    )
    # A top-level child crash/timeout is one health-signal error *and* a raised-crawl
    # flag: the enumeration is partial, so closure must be skipped this run.
    crawl_raised = result.exception is not None
    errors = result.errors + (1 if crawl_raised else 0)
    return _process(
        slug,
        run_id,
        is_first,
        result.items,
        errors,
        crawl_raised,
        result.pages_fetched,
        store,
        matcher=matcher,
        enumerates_all=enumerates_all,
        expected_positions=expected_positions,
        first_crawl_cap=first_crawl_cap,
        min_description_chars=min_description_chars,
        expired_markers=expired_markers,
        close_after_misses=close_after_misses,
        match_min_score=match_min_score,
    )


def _process(
    slug: str,
    run_id: int,
    is_first: bool,
    jobs: list[Job],
    errors: int,
    crawl_raised: bool,
    pages_fetched: int,
    store: StateStore,
    *,
    matcher: Matcher | None,
    enumerates_all: bool,
    expected_positions: int,
    first_crawl_cap: int | None,
    min_description_chars: int,
    expired_markers: tuple[str, ...],
    close_after_misses: int,
    match_min_score: int,
) -> CrawlSummary:
    """Track → match → close already-collected jobs; the shared back half of both paths.

    Only *how* the jobs were produced differs upstream (in-process vs. subprocess); the
    completeness/expiry floors, the lifecycle upsert, matching, the guarded closure diff,
    and the run record are identical, so they live here once.
    """
    # The operator's denylist (§11) is load-bearing on the dedup key (§4.6): two URLs
    # differing only on a tracking param must collapse to one canonical position.
    drop = config.tracking_param_denylist()
    markers = tuple(m.lower() for m in expired_markers)

    # ``present`` = COMPLETE positions tracked this crawl; it gates the mass-closure guard
    # (a wholly-incomplete crawl leaves it empty → no diff-close). ``seen`` = every URL
    # served ALIVE this crawl, complete OR truncated; it is the closure diff's alive set,
    # so a momentarily-truncated re-serve isn't aged toward closure (§4.7).
    present: set[str] = set()
    seen: set[str] = set()
    incomplete = 0
    new_positions = 0
    changed_positions = 0
    new_or_changed: list[tuple[str, EnrichedJob]] = []
    unchanged: list[tuple[str, EnrichedJob]] = []

    for job in jobs:
        canon = canonicalize_url(job.url, drop_params=drop)
        description = job.description or ""
        # Expired: the board still serves the page but the role is gone (§4.7). Close an
        # open row now (idempotent; a no-op for an untracked posting); never track/match.
        # Kept OUT of ``seen`` so the closure diff ignores it (it's already closed).
        if markers and _has_marker(description, markers):
            store.mark_closed(slug, canon, reason="expired")
            continue
        # Served alive this crawl — record BEFORE the completeness floor so a truncated
        # re-serve stays in the closure diff's alive set (``seen``) and can't age a live
        # posting toward closure, even though it is not tracked/matched (finding: the
        # completeness floor must not punch a hole in the closure ``present`` set).
        seen.add(canon)
        # Completeness floor (§4.6): a stub/truncated description is dropped and left
        # untracked — a later, complete fetch upserts it fresh — never counted complete, so
        # a wholly-incomplete crawl leaves ``present`` empty and the guard skips closure.
        if (
            min_description_chars > 0
            and len(description.strip()) < min_description_chars
        ):
            incomplete += 1
            continue
        # Enrich once (§4.6, deterministic, no LLM): the derived classification populates
        # the position row AND feeds the prefilter + matcher (new/changed now, or an
        # unchanged posting the drain re-presents when it is still parked pending).
        enriched = enrich(job)
        outcome = store.upsert_position(
            slug,
            canon,
            fields=_position_fields(job, enriched),
            content_hash=_content_hash(job),
        )
        present.add(canon)
        if outcome == "new":
            new_positions += 1
            new_or_changed.append((canon, enriched))
        elif (
            outcome == "changed"
        ):  # edited or reopened → re-surface + (cache-free) re-match
            changed_positions += 1
            new_or_changed.append((canon, enriched))
        else:  # 'unchanged': cache would hit for a scored row, but a cap-parked row is a
            unchanged.append(
                (canon, enriched)
            )  # never-scored MISS the drain re-presents.

    matched, match_pending = _match(
        slug,
        new_or_changed,
        unchanged,
        store,
        matcher,
        is_first,
        first_crawl_cap,
        match_min_score,
    )

    # Enumeration floor (§6 discipline, runtime half). When the board's open-role total
    # is known (``expected_positions`` > 0, pinned per-site in the registry), a crawl that
    # surfaced far fewer alive than that — below ``enum_floor_ratio`` of it — enumerated
    # only a *slice*: pagination broke, a selector drifted, an ATS shifted its response
    # shape. Such a crawl need not raise, so ``crawl_raised`` alone can't catch it, yet
    # closing the board's tail against that slice would mass-false-close live roles. Treat
    # it as a partial run and skip the diff, exactly as a raised crawl does. Dormant at
    # ``expected_positions == 0`` (the default: no estimate, e.g. a windowed source), so
    # existing registries keep prior behaviour. Keyed on ``len(seen)`` (distinct alive
    # URLs, the closure diff's own alive set), not the raw yield, so duplicate yields
    # can't inflate a slice past the floor.
    floor = math.ceil(config.enum_floor_ratio() * expected_positions)
    under_enumerated = expected_positions > 0 and len(seen) < floor

    # Closure diff (§4.7) — the mass-closure guard, a correctness invariant. Only a
    # healthy full-enumeration run may age out unseen positions: the source enumerates
    # its whole board, the crawl did not raise (a partial/aborted enumeration must not
    # close the tail), it cleared the enumeration floor above, and >= 1 COMPLETE position
    # was tracked (an empty/all-incomplete run leaves ``present`` empty and must never
    # close the whole board). The diff itself runs against ``seen`` (complete + truncated)
    # so a truncated re-serve stays alive.
    closed_positions = 0
    if enumerates_all and not crawl_raised and present and not under_enumerated:
        closed_positions = len(
            store.close_missing(slug, seen, threshold=close_after_misses)
        )

    positions_found = len(jobs)
    # §4.8 partial-drift telemetry (P0). The signal key stays ``body_quality`` — the
    # landed health/metrics drift consumers (health.body_quality_collapse,
    # metrics.mean_body_quality) read that exact key; renaming it is a cross-cutting
    # change beyond the runner. It is the fraction of found positions whose description
    # cleared the completeness bar — it sags when a site starts serving stubs even while
    # positions_found stays healthy. Host-side only, never surfaced to the generator (§8);
    # omitted on a zero-yield crawl (the blackout signal is positions_found == 0 itself).
    signals: dict[str, object] = {"incomplete": incomplete}
    if positions_found:
        signals["body_quality"] = (positions_found - incomplete) / positions_found
    # §4.8 under-enumeration signal: a known board that came back a slice. Recorded even
    # though closure was skipped above, so the operator sees *why* the tail stopped aging
    # (a silently-broken crawler that never raised) instead of a mystery frozen board.
    if under_enumerated:
        signals["under_enumerated"] = {
            "seen": len(seen),
            "floor": floor,
            "expected": expected_positions,
        }

    store.finish_run(
        run_id,
        items_found=positions_found,
        new_items=new_positions,
        errors=errors,
        pages_fetched=pages_fetched,
        signals=signals,
    )
    return CrawlSummary(
        slug,
        positions_found,
        new_positions,
        changed_positions,
        closed_positions,
        errors,
        pages_fetched,
        incomplete,
        matched,
        match_pending,
    )


def _match(
    slug: str,
    new_or_changed: list[tuple[str, EnrichedJob]],
    unchanged: list[tuple[str, EnrichedJob]],
    store: StateStore,
    matcher: Matcher | None,
    is_first: bool,
    first_crawl_cap: int | None,
    match_min_score: int,
) -> tuple[int, int]:
    """Prefilter → cap → score → notify new/changed, and drain the re-seen backlog (§4.7).

    Returns ``(matched, match_pending)``. A ``None`` matcher (no profile / autonomy off)
    short-circuits: the crawl still tracked everything, it simply is not scored — never a
    crash. :meth:`Matcher.score_new` is itself fail-open (a matching outage parks jobs
    pending, never raises into the crawl loop), so this whole block is safe.

    ``unchanged`` are re-seen positions whose ``content_hash`` didn't move. An already-scored
    one is a free cache hit we deliberately do NOT re-present (re-applying it would re-notify
    the same match every crawl); but one still flagged ``match_pending`` — parked over a
    prior daily/first-crawl cap and never LLM-scored — is a cache MISS a plain re-crawl would
    starve forever. So on a non-first crawl we drain exactly those, re-presenting them for
    real scoring within budget ("drained by later runs", §4.7). The free pre-LLM
    ``prefilter`` is §6/§8; ``score_new`` caps the combined load, so the drain can't overspend.
    """
    if matcher is None:
        return 0, 0

    # The matcher owns the profile; reuse its prefs for the free, deterministic pre-LLM
    # filter (§6/§8) so we never spend a token on a definite mismatch.
    prefs = matcher._profile.prefs
    kept = [(canon, e) for canon, e in new_or_changed if prefilter(e, prefs)[0]]

    # First-crawl cap bounds only the MATCHING load: score the freshest K now, park the
    # tail pending (drained by later runs) so a standing back-catalogue can't blow the
    # daily budget on a site's first crawl. Everything is already tracked regardless.
    to_score, deferred = _first_crawl_split(kept, is_first, first_crawl_cap)
    match_pending = 0
    for canon, _e in deferred:
        store.set_match_pending(slug, canon)
        match_pending += 1

    # Drain the backlog (§4.7): re-present positions re-seen unchanged this crawl that are
    # STILL parked pending (a never-scored cap miss). Skipped on a first crawl — that pass
    # establishes the backlog via the first-crawl cap rather than draining it. New/changed
    # lead the combined batch so fresh postings out-rank the backlog when the cap bites; a
    # parked row that is now a definite prefilter miss simply stays parked.
    if not is_first and unchanged:
        parked = set(store.pending_urls(slug, limit=10_000))
        to_score += [
            (canon, e)
            for canon, e in unchanged
            if canon in parked and prefilter(e, prefs)[0]
        ]

    result = matcher.score_new([(slug, canon, e) for canon, e in to_score])
    matched = cast(int, result["cached"]) + cast(int, result["scored"])
    match_pending += cast(int, result["pending"])

    # Notify strong matches (§4.7 pipeline sink). notify() is best-effort and silent when
    # no webhook is configured (that is notify.py's job) — the runner just calls it.
    sent = {canon: e for canon, e in to_score}
    results = cast("list[tuple[str, str, MatchVerdict]]", result["results"])
    for site, url, verdict in results:
        if verdict.score < match_min_score:
            continue
        e = sent.get(url)
        if e is None:  # defensive: every result url was sent, but never assume
            continue
        job = e.job
        notify.notify(
            "match",
            f"{job.title} at {job.company} — {verdict.verdict} ({verdict.score})",
            site=site,
            url=url,
            title=job.title,
            company=job.company,
            score=verdict.score,
            reasons="; ".join(verdict.reasons),
        )
    return matched, match_pending


def probe_crawler(
    crawl_fn: CrawlFn,
    ctx: Ctx,
    *,
    min_description_chars: int = 0,
    expired_markers: tuple[str, ...] = (),
) -> int:
    """Run a crawler and count its **complete, live** positions — no track, no match (§4.8).

    The re-probe that confirms a staleness candidate is a *real* failure rather than a
    transient outage: re-run the crawler and see whether it still parses >= 1 complete,
    non-expired posting. Mirrors the crawl path's collect + floors (so an all-stub or
    all-expired result counts as zero), but never upserts, matches, or touches the store.
    XML is hardened first, since the crawler may parse an attacker feed (§8).
    """
    harden_xml()
    jobs, _errors, _raised = _collect(crawl_fn, ctx)
    return _complete_count(jobs, min_description_chars, expired_markers)


def probe_isolated(
    source: str,
    allowed_hosts: Collection[str],
    *,
    min_description_chars: int = 0,
    expired_markers: tuple[str, ...] = (),
    timeout: float = 600.0,
    mem_mb: int = 1024,
    cpu_seconds: float = 120.0,
) -> int:
    """Subprocess-isolated §4.8 re-probe: count complete, live positions; no track/match.

    The isolated sibling of :func:`probe_crawler`. The self-healer's confirm-a-real-
    failure re-run must itself be bounded (a drifted crawler can hang on the reprobe too)
    and must never raise into the heal loop, so a hang / crash / gate rejection counts
    zero complete positions — a confirmed failure, exactly as a live run would.
    """
    result = collect_crawler(
        source,
        allowed_hosts=allowed_hosts,
        timeout=timeout,
        mem_mb=mem_mb,
        cpu_seconds=cpu_seconds,
    )
    return _complete_count(result.items, min_description_chars, expired_markers)


# --- collect + floors ------------------------------------------------------


def _collect(crawl_fn: CrawlFn, ctx: Ctx) -> tuple[list[Job], int, bool]:
    """Drain the crawler, isolating any failure to this site (§4.4).

    Returns ``(jobs, errors, crawl_raised)``. ``crawl_raised`` marks a top-level
    exception mid-crawl: the enumeration was cut short, so the caller must skip the
    closure diff (a partial run must never age out the unseen tail).
    """
    jobs: list[Job] = []
    errors = 0
    try:
        for obj in crawl_fn(ctx):
            if isinstance(obj, Job):
                jobs.append(obj)
                continue
            try:
                # Generated crawlers commonly yield plain dicts; validate them into Jobs
                # exactly as the §4.4 sandbox does, so a candidate that passes validation
                # also runs at crawl time. A genuinely invalid yield is the error.
                jobs.append(Job.model_validate(obj))
            except Exception:
                errors += 1
    except Exception:  # untrusted generated code — never abort the fleet
        return jobs, errors + 1, True
    return jobs, errors, False


def _complete_count(
    jobs: list[Job], min_description_chars: int, expired_markers: tuple[str, ...]
) -> int:
    """Count jobs that clear the completeness floor and carry no expired marker (§4.8)."""
    markers = tuple(m.lower() for m in expired_markers)
    n = 0
    for job in jobs:
        description = job.description or ""
        if markers and _has_marker(description, markers):
            continue
        if (
            min_description_chars > 0
            and len(description.strip()) < min_description_chars
        ):
            continue
        n += 1
    return n


def _has_marker(description: str, markers: tuple[str, ...]) -> bool:
    """True if any (pre-lowercased) expired marker is a substring of the description."""
    low = description.lower()
    return any(m in low for m in markers)


# --- position fields + content hash ----------------------------------------


def _position_fields(job: Job, enriched: EnrichedJob) -> dict[str, object]:
    """The ``positions`` columns for one job: raw identity + the §4.6 enrichment.

    Salary columns are written ONLY when the enrichment carries structured compensation
    (the crawler's own fields, or the §4.6 host parse). A salary-less crawl OMITS them
    rather than sending NULLs, so an upsert never nulls out a salary the §4.7 match-time
    extractor backfilled onto the row (``set_salary``); when the source does state a
    salary it is included and wins on upsert — a source figure always beats an LLM guess.

    ``description`` is persisted verbatim so the row is a complete record of the
    posting: the §4.7 matcher can rebuild its LLM digest (and verify ``content_hash``)
    from the store alone, which is what lets a profile edit re-score the entire backlog
    without waiting for every board's next crawl to re-serve the text.
    """
    fields: dict[str, object] = {
        "external_id": job.external_id,
        "apply_url": job.apply_url,
        "title": job.title,
        "company": job.company,
        # Derived §16 dedup key, refreshed on every crawl. Kept OUT of ``content_hash``
        # (that hashes the verbatim ``company``), so recomputing it is churn-free.
        "company_canonical": canonical_company(job.company),
        "location": job.location,
        # Canonical display/grouping form of the verbatim ``location``. Like
        # ``company_canonical`` it is kept OUT of ``content_hash`` (which hashes the
        # raw ``location``), so recomputing it on every crawl is churn-free.
        "location_normalized": normalize_location(job.location),
        "description": job.description,
        "discipline": enriched.discipline,
        "level": enriched.level,
        "workplace": enriched.workplace,
        "remote_scope": enriched.remote_scope,
        "employment_type": job.employment_type,
        "department": job.department,
        "posted_at": job.posted_at,
        "expires_at": job.expires_at,
    }
    salary = enriched.salary
    if salary is not None:
        fields["salary_min"] = salary.min
        fields["salary_max"] = salary.max
        fields["salary_currency"] = salary.currency
        fields["salary_period"] = salary.period
    return fields


def _content_hash(job: Job) -> str:
    """Hash a job's identity content — title/company/location/description.

    MUST mirror :func:`jobcrawler.match._content_hash` byte-for-byte: whitespace-
    normalized fields joined by newlines and sha256-hexed. Sharing the formula is what
    keeps a job's ``positions.content_hash`` and its match-cache key identical, so an
    unchanged posting re-scores from cache for free and any content edit re-hashes and
    re-scores (§4.7/§6).
    """
    parts = [
        _norm_ws(job.title),
        _norm_ws(job.company),
        _norm_ws(job.location),
        _norm_ws(job.description),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _norm_ws(value: object) -> str:
    """Collapse all runs of whitespace to single spaces and strip (hash stability)."""
    return " ".join(str(value or "").split())


# --- first-crawl matching cap ----------------------------------------------


def _first_crawl_split(
    kept: list[tuple[str, EnrichedJob]], is_first: bool, cap: int | None
) -> tuple[list[tuple[str, EnrichedJob]], list[tuple[str, EnrichedJob]]]:
    """On a first crawl, split prefiltered candidates into (freshest K to score, tail).

    Only bites on a site's first position-yielding crawl and only when the candidate
    count exceeds ``cap``; otherwise everything scores now. Freshness is ``posted_at``
    newest-first, undated last (they can't be ranked fresh) — so the tail parked pending
    is the stalest of the standing back-catalogue.
    """
    if not is_first or cap is None or len(kept) <= cap:
        return kept, []
    ordered = sorted(kept, key=lambda ce: _freshness(ce[1].job), reverse=True)
    return ordered[:cap], ordered[cap:]


def _freshness(job: Job) -> tuple[int, float]:
    """Sort key: dated jobs newest-first, undated last (can't be ranked fresh)."""
    dt = job.posted_at
    if dt is None:
        return (0, 0.0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (1, dt.timestamp())
