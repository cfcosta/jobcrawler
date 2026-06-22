"""The crawl runner (§4.4): import → crawl → normalize → dedup → ingest.

One runner executes any crawler. It calls ``crawl(ctx)``, validates + dedups the
yielded items against crawl-state, ingests new ones per-item via ``POST /news``,
and writes a §5 Run record. These policies live here (the state store is dumb):

  * **Failure isolation (§4.4).** A script that raises, yields a non-Item, or
    finds nothing is recorded as a health signal on *this* site — partial items
    already collected are still processed; the run never aborts the fleet.
  * **Failure-class re-attempt (§4.7).** INGESTED → mark ingested + count new;
    RETRY (pre-insert failure) → leave the URL unseen so the next crawl retries;
    TERMINAL (5xx / after-send) → mark seen so it is never re-sent (a re-send is
    duplicate server-side LLM spend, not just a duplicate row).
  * **Only ingest complete (§4.6).** An item whose body is below ``min_body_chars``
    is a paywall teaser / truncated extraction; it is dropped before ingest and
    left *unseen* (so a later, complete fetch can still be picked up). The floor is
    a host-side guarantee — independent of what the untrusted generated crawler
    decides to yield. It is opt-in (default 0 = off) and wired to ``config`` by the
    CLI, so the runner's other callers keep their prior behavior.

On a site's *first* crawl an optional newest-K cap (``AUTOCRAWLER_FIRST_CRAWL_CAP``)
ingests only the freshest items and marks the historical tail seen, so a large
back-catalog never floods the news API's LLM enrichment.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import timezone

from bizup_autocrawler import config
from bizup_autocrawler.governor import RateGovernor
from bizup_autocrawler.ingest import IngestOutcome, NewsClient
from bizup_autocrawler.runtime.contract import CrawlFn, Item
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.hardening import harden_xml
from bizup_autocrawler.runtime.sandbox import collect_crawler
from bizup_autocrawler.state import StateStore
from bizup_autocrawler.urls import canonicalize_url


@dataclass(frozen=True, slots=True)
class CrawlSummary:
    """The §5 Run record counters for one crawl."""

    site: str
    items_found: int
    new_items: int
    errors: int
    pages_fetched: int
    incomplete: int = 0  # yielded but dropped as below the completeness floor (§4.6)


def crawl_site(
    slug: str,
    crawl_fn: CrawlFn,
    ctx: Ctx,
    store: StateStore,
    news: NewsClient,
    *,
    first_crawl_cap: int | None = None,
    governor: RateGovernor | None = None,
    min_body_chars: int = 0,
    paywall_markers: tuple[str, ...] = (),
    crawler_name: str = "",
    crawler_url: str = "",
) -> CrawlSummary:
    """In-process crawl: import → crawl → normalize → dedup → ingest.

    The path the M0 reference crawler and the test surface drive directly, holding
    the ``crawl_fn`` + ``ctx`` themselves. Production runs *committed* scripts through
    :func:`crawl_site_isolated` instead (subprocess-bounded, §4.4); this in-process
    form trusts its caller to have already bounded the crawler.
    """
    harden_xml()  # XXE / billion-laughs off before the crawler parses feeds (§8)
    is_first = store.last_run(slug) is None
    run_id = store.start_run(slug)
    items, errors = _collect(crawl_fn, ctx)
    return _ingest_collected(
        slug,
        run_id,
        is_first,
        items,
        errors,
        ctx.pages_fetched,
        store,
        news,
        first_crawl_cap=first_crawl_cap,
        governor=governor,
        min_body_chars=min_body_chars,
        paywall_markers=paywall_markers,
        crawler_name=crawler_name,
        crawler_url=crawler_url,
    )


def crawl_site_isolated(
    slug: str,
    source: str,
    allowed_hosts: Collection[str],
    store: StateStore,
    news: NewsClient,
    *,
    timeout: float = 600.0,
    mem_mb: int = 1024,
    cpu_seconds: float = 120.0,
    first_crawl_cap: int | None = None,
    governor: RateGovernor | None = None,
    min_body_chars: int = 0,
    paywall_markers: tuple[str, ...] = (),
    crawler_name: str = "",
    crawler_url: str = "",
) -> CrawlSummary:
    """Run a *committed* crawler in a bounded subprocess, then ingest host-side (§4.4).

    The production crawl path. The script executes in the §4.4 child (AST gate +
    RLIMIT_AS / RLIMIT_CPU + wall-clock), so a runaway crawler (infinite loop, memory
    blowup) is bounded and reported rather than wedging or OOMing the daemon. The
    child only fetches + yields items; the host keeps ownership of dedup, the
    completeness filter, governor-paced ingest, and the run record — the child never
    touches the news API or the state store. A hang / crash / gate rejection comes
    back as a failed run (0 items, ≥1 error), never a raise (§4.4 failure isolation).
    XML hardening runs inside the child, so it is not repeated here.
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
    # A top-level child crash/timeout is one health-signal error, mirroring the
    # in-process _collect (a raise mid-crawl → +1 error, partial items kept).
    errors = result.errors + (1 if result.exception is not None else 0)
    return _ingest_collected(
        slug,
        run_id,
        is_first,
        result.items,
        errors,
        result.pages_fetched,
        store,
        news,
        first_crawl_cap=first_crawl_cap,
        governor=governor,
        min_body_chars=min_body_chars,
        paywall_markers=paywall_markers,
        crawler_name=crawler_name,
        crawler_url=crawler_url,
    )


def _ingest_collected(
    slug: str,
    run_id: int,
    is_first: bool,
    items: list[Item],
    errors: int,
    pages_fetched: int,
    store: StateStore,
    news: NewsClient,
    *,
    first_crawl_cap: int | None = None,
    governor: RateGovernor | None = None,
    min_body_chars: int = 0,
    paywall_markers: tuple[str, ...] = (),
    crawler_name: str = "",
    crawler_url: str = "",
) -> CrawlSummary:
    """Dedup → completeness-filter → governor-paced ingest of already-collected items.

    The shared back half of both crawl paths (in-process and subprocess-isolated):
    only *how* the items were produced differs upstream; the dedup, ingest, and run
    record are identical, so they live here once.
    """
    # Pace ingestion to the news-API LLM capacity (§4.7) — distinct from the §4.5
    # per-host *fetch* cap. Each POST drives 2–3 server-side LLM calls, so a burst
    # (a first crawl, several sites finishing at once) would overrun enrichment.
    governor = governor or RateGovernor(config.ingest_rate())

    # Only ingest complete (§4.6): teasers/truncated bodies are dropped here, never
    # marked seen — a later complete fetch of the same URL can still be ingested.
    complete = _complete_items(items, min_body_chars, paywall_markers)
    fresh, historical = _apply_first_crawl_cap(complete, is_first, first_crawl_cap)

    # The operator's denylist (§11) is load-bearing on the dedup key (§4.6): two
    # URLs differing only on a tracking param must collapse to one.
    drop = config.tracking_param_denylist()

    # capped-out back-catalog: mark seen so it neither ingests now nor backfills later.
    for item in historical:
        store.mark_seen(slug, canonicalize_url(item.url, drop_params=drop))

    new_items = 0
    for item in fresh:
        canon = canonicalize_url(item.url, drop_params=drop)
        if store.is_seen(slug, canon):
            continue
        governor.wait()  # throttle to AUTOCRAWLER_INGEST_RATE before the POST
        outcome = news.post_item(
            item.title,
            item.body,
            crawler={"slug": slug, "name": crawler_name, "url": crawler_url},
        )
        if outcome is IngestOutcome.INGESTED:
            store.mark_ingested(slug, canon)
            new_items += 1
        elif outcome is IngestOutcome.TERMINAL:
            store.mark_seen(slug, canon)  # never re-send: would duplicate LLM spend
            errors += 1
        else:  # RETRY: leave unseen so the next crawl re-attempts the POST
            errors += 1

    store.finish_run(
        run_id,
        items_found=len(items),
        new_items=new_items,
        errors=errors,
        pages_fetched=pages_fetched,
    )
    return CrawlSummary(
        slug,
        len(items),
        new_items,
        errors,
        pages_fetched,
        len(items) - len(complete),
    )


def probe_crawler(
    crawl_fn: CrawlFn,
    ctx: Ctx,
    *,
    min_body_chars: int = 0,
    paywall_markers: tuple[str, ...] = (),
) -> int:
    """Run a crawler and count its **complete** items — no ingest, no state (§4.8).

    The re-probe that confirms a staleness candidate is a *real* failure rather than
    a transient outage: re-run the crawler and see whether it still parses >= 1
    complete item. Mirrors the crawl path's collect + completeness filter (so a
    paywalled-teaser-only result counts as zero), but never POSTs or touches the
    store. XML is hardened first, since the crawler may parse an attacker feed (§8).
    """
    harden_xml()
    items, _ = _collect(crawl_fn, ctx)
    return len(_complete_items(items, min_body_chars, paywall_markers))


def probe_isolated(
    source: str,
    allowed_hosts: Collection[str],
    *,
    min_body_chars: int = 0,
    paywall_markers: tuple[str, ...] = (),
    timeout: float = 600.0,
    mem_mb: int = 1024,
    cpu_seconds: float = 120.0,
) -> int:
    """Subprocess-isolated §4.8 re-probe: count complete items, no ingest, no state.

    The isolated sibling of :func:`probe_crawler`. The self-healer's confirm-a-real-
    failure re-run must itself be bounded (a drifted crawler can hang on the reprobe
    too) and must never raise into the heal loop, so a hang / crash / gate rejection
    counts zero complete items — a confirmed failure, exactly as a live run would.
    """
    result = collect_crawler(
        source,
        allowed_hosts=allowed_hosts,
        timeout=timeout,
        mem_mb=mem_mb,
        cpu_seconds=cpu_seconds,
    )
    return len(_complete_items(result.items, min_body_chars, paywall_markers))


def _collect(crawl_fn: CrawlFn, ctx: Ctx) -> tuple[list[Item], int]:
    """Drain the crawler, isolating any failure to this site (§4.4)."""
    items: list[Item] = []
    errors = 0
    try:
        for obj in crawl_fn(ctx):
            if isinstance(obj, Item):
                items.append(obj)
                continue
            try:
                # Generated crawlers commonly yield plain dicts; validate them into
                # Items exactly as the §4.4 sandbox does, so a candidate that passes
                # validate_crawler also runs at crawl time. A genuinely invalid yield
                # is the error.
                items.append(Item.model_validate(obj))
            except Exception:
                errors += 1
    except Exception:  # untrusted generated code — never abort the fleet
        errors += 1
    return items, errors


def _complete_items(
    items: list[Item], min_body_chars: int, paywall_markers: tuple[str, ...] = ()
) -> list[Item]:
    """Keep only items whose body is a complete article (§4.6).

    An item is dropped if its body is below ``min_body_chars`` *or* contains a
    paywall-teaser marker (a standfirst + teaser + "subscribe" pitch can clear the
    length floor yet not be a full article). No filter active (floor <= 0 and no
    markers) returns the list unchanged, so non-CLI callers are unaffected. A
    dropped item is omitted, never marked seen — a later complete fetch still wins.
    """
    if min_body_chars <= 0 and not paywall_markers:
        return items
    markers = tuple(m.lower() for m in paywall_markers)
    kept: list[Item] = []
    for item in items:
        body = item.body.strip()
        if min_body_chars > 0 and len(body) < min_body_chars:
            continue
        if markers and any(m in body.lower() for m in markers):
            continue
        kept.append(item)
    return kept


def _apply_first_crawl_cap(
    items: list[Item], is_first: bool, cap: int | None
) -> tuple[list[Item], list[Item]]:
    """On a first crawl, split into (newest-K to ingest, older tail to skip)."""
    if not is_first or cap is None or len(items) <= cap:
        return items, []
    ordered = sorted(items, key=_freshness, reverse=True)
    return ordered[:cap], ordered[cap:]


def _freshness(item: Item) -> tuple[int, float]:
    """Sort key: dated items newest-first, undated last (can't be ranked fresh)."""
    dt = item.published_at
    if dt is None:
        return (0, 0.0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (1, dt.timestamp())
