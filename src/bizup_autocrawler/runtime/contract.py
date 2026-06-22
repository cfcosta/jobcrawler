"""The crawler contract: the normalized Item a crawler yields (§4.6).

A generated ``crawlers/<slug>.py`` exposes ``crawl(ctx) -> Iterable[Item]``;
the runner validates each yielded item against this model before dedup and
ingest. ``title`` and ``body`` map to ``POST /news {title, body}``; ``url`` is
the canonical dedup key (see ``bizup_autocrawler.urls``); ``source`` is the
site slug. The required strings must be non-empty — an item with an empty
title or body is never valid (the §6 gates apply the stricter length checks).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field


class Item(BaseModel):
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    published_at: datetime | None = None
    source: str = Field(min_length=1)


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


CrawlFn = Callable[[Ctx], Iterable[Item]]


def load_crawler(module: object) -> tuple[str, CrawlFn]:
    """Return ``(SITE, crawl)`` from a validated crawler module (§4.3)."""
    site = getattr(module, "SITE", None)
    crawl = getattr(module, "crawl", None)
    if not isinstance(site, str) or not site:
        raise TypeError("crawler module must define a non-empty SITE: str")
    if not callable(crawl):
        raise TypeError("crawler module must define crawl(ctx)")
    return site, cast(CrawlFn, crawl)
