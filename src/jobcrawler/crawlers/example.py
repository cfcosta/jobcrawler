"""Hand-written reference crawler — the M0 "no LLM" path proof (§4.4).

This is the committed artifact that proves the whole pipeline end to end with
zero generation: the runner imports and executes it exactly as it will execute a
generated ``crawlers/<slug>.py``, so a green run here means
import → crawl → normalize → lifecycle (dedup / close-missing) → match works
before any LLM is involved. It stands in for the simplest real shape — a single
ATS-style JSON endpoint that returns the whole board in one call — and is
deliberately dependency-minimal: the allowlisted ``json`` module only (no parser
deps, no ``os`` / ``socket`` / ``open``), every fetch through ``ctx`` so the §8
SSRF guard and §4.5 politeness apply, and it passes the §6 AST gate unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from jobcrawler.runtime.contract import Ctx, Job

SITE = "https://example.com"
_API = f"{SITE}/api/jobs"


def crawl(ctx: Ctx) -> Iterable[Job]:
    # One GET returns the whole board as JSON — ``{"jobs": [{...}, ...]}``. ``json``
    # is on the sandbox allowlist; ``ctx.fetch`` hands back the decoded body as text.
    payload = json.loads(ctx.fetch(_API))
    for row in payload.get("jobs", []):
        external_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        company = str(row.get("company") or "").strip()
        location = str(row.get("location") or "").strip()
        description = str(row.get("description") or "").strip()
        # The five content strings are required and non-empty (§4.6): skip a row
        # missing any rather than yield a Job that would fail validation downstream.
        if not (external_id and title and company and location and description):
            continue
        yield Job(
            url=f"{SITE}/jobs/{external_id}",
            title=title,
            company=company,
            location=location,
            description=description,
            # Optional enrichment — set only when the row states it, left None
            # otherwise. ``posted_at`` is an ISO-8601 string; pydantic coerces it
            # to a datetime (the crawler never parses dates itself, §4.6).
            employment_type=row.get("employment_type"),
            posted_at=row.get("posted_at"),
            external_id=external_id,
            source="example",
        )
