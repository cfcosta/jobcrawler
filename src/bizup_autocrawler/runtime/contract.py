"""The crawler contract: the normalized Item a crawler yields (§4.6).

A generated ``crawlers/<slug>.py`` exposes ``crawl(ctx) -> Iterable[Item]``;
the runner validates each yielded item against this model before dedup and
ingest. ``title`` and ``body`` map to ``POST /news {title, body}``; ``url`` is
the canonical dedup key (see ``bizup_autocrawler.urls``); ``source`` is the
site slug. The required strings must be non-empty — an item with an empty
title or body is never valid (the §6 gates apply the stricter length checks).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Item(BaseModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    published_at: datetime | None = None
    source: str = Field(min_length=1)
