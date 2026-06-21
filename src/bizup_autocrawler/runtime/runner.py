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

from dataclasses import dataclass
from datetime import timezone

from bizup_autocrawler import config
from bizup_autocrawler.governor import RateGovernor
from bizup_autocrawler.ingest import IngestOutcome, NewsClient
from bizup_autocrawler.runtime.contract import CrawlFn, Item
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.hardening import harden_xml
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
) -> CrawlSummary:
    harden_xml()  # XXE / billion-laughs off before the crawler parses feeds (§8)
    is_first = store.last_run(slug) is None
    run_id = store.start_run(slug)

    # Pace ingestion to the news-API LLM capacity (§4.7) — distinct from the §4.5
    # per-host *fetch* cap. Each POST drives 2–3 server-side LLM calls, so a burst
    # (a first crawl, several sites finishing at once) would overrun enrichment.
    governor = governor or RateGovernor(config.ingest_rate())

    items, errors = _collect(crawl_fn, ctx)
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
        outcome = news.post_item(item.title, item.body)
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
        pages_fetched=ctx.pages_fetched,
    )
    return CrawlSummary(
        slug,
        len(items),
        new_items,
        errors,
        ctx.pages_fetched,
        len(items) - len(complete),
    )


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
