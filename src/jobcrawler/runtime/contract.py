"""The crawler contract: the normalized :class:`Job` a crawler yields (§4.6).

A generated ``crawlers/<slug>.py`` exposes ``crawl(ctx) -> Iterable[Job]``; the
runner validates each yielded job against this model before canonicalizing,
hashing, and upserting it into the ``positions`` store (§4.7). ``url`` is the
canonical dedup key (see :mod:`jobcrawler.urls`); ``source`` is the site slug.

**Required vs. extract-when-present.** The five content strings plus ``source``
are *required and non-empty* — a posting with no title/company/location/
description is not a job we can track, match, or de-dupe, so it never validates
(the §6 gates layer the stricter length/quality checks on top). Everything else
is *optional*: a crawler fills a field only when the source actually states it,
leaving it ``None`` otherwise. That split is deliberate — ATS boards differ
wildly in what they expose (Greenhouse has no salary/employment fields at all;
Lever always has ``workplaceType``; Ashby has structured compensation ~70% of
the time) — so the optional fields are opportunistic enrichment, never a
contract the generator must satisfy.

**Never invent salary (§4.6).** ``salary`` is the *verbatim* compensation text
as printed; the structured ``salary_min``/``salary_max``/``salary_currency``/
``salary_period`` are set only when the source exposes them as structured data.
A crawler must never parse, guess, or synthesize a number the page did not
state — the host-side deterministic parser (:func:`jobcrawler.runtime.normalize.
parse_salary`) is the single place text→number extraction happens, and it too is
conservative (``None`` when ambiguous). Raw fields (``workplace_type``,
``salary_period``, ``employment_type``) are stored *as stated*; normalization
into the closed taxonomies lives host-side in :mod:`normalize`, never in the
untrusted crawler.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field


class Job(BaseModel):
    """One open position a crawler yields, validated before dedup/upsert (§4.6)."""

    # --- required: the identity + content every trackable posting must have ---
    url: str = Field(min_length=1)  # canonical dedup key (jobcrawler.urls)
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    location: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: str = Field(min_length=1)  # site slug

    # --- optional: filled only when the source actually states it ------------
    # Compensation. ``salary`` is the verbatim printed text; the structured
    # quartet is set only from source-structured data (never parsed by the
    # crawler — that is normalize.parse_salary's job, host-side).
    salary: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # 'year' | 'month' | 'hour' as stated

    # Classification inputs, stored raw (host-side normalize.py maps them into
    # the closed taxonomies — see §4.6/§8: no interpretation in the crawler).
    employment_type: str | None = None
    workplace_type: str | None = None  # raw 'remote'/'hybrid'/'onsite' as stated
    department: str | None = None

    # Lifecycle timestamps (drive §4.9 recheck + expiry-based closure).
    posted_at: datetime | None = None
    expires_at: datetime | None = None

    # Stable ATS identity + the canonical apply link.
    external_id: str | None = None  # stable ATS id, when the source exposes one
    apply_url: str | None = None


@runtime_checkable
class Ctx(Protocol):
    """The single fetch boundary a crawler is handed (§4.5).

    All network access goes through ``ctx`` so timeouts, the SSRF guard, and
    encoding are enforced centrally now, and robots / rate-limit / Cloudflare
    join them with the M2 politeness layer — the script declares *what* to
    fetch, not *how*.
    """

    def fetch(self, url: str) -> str: ...
    def render(self, url: str) -> str: ...
    def get(self, url: str, **kwargs: object) -> object: ...
    def log(self, *args: object, **kwargs: object) -> None: ...


CrawlFn = Callable[[Ctx], Iterable[Job]]


def load_crawler(module: object) -> tuple[str, CrawlFn]:
    """Return ``(SITE, crawl)`` from a validated crawler module (§4.3)."""
    site = getattr(module, "SITE", None)
    crawl = getattr(module, "crawl", None)
    if not isinstance(site, str) or not site:
        raise TypeError("crawler module must define a non-empty SITE: str")
    if not callable(crawl):
        raise TypeError("crawler module must define crawl(ctx)")
    return site, cast(CrawlFn, crawl)
