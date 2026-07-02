"""Hand-written reference crawler (M0 path proof, §4.4).

This is the M0 "no LLM" artifact: a committed ``crawlers/<slug>.py`` that the
runner imports and executes exactly as it will execute a generated one. It
doubles as a contract test for the import → crawl → normalize → dedup → ingest
path. It parses a tiny article-index with the allowlisted stdlib only (``re`` —
no parser deps, no ``os``/``socket``/``open``), and fetches solely through
``ctx`` so the §8 SSRF guard and §4.5 politeness apply.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bizup_autocrawler.runtime.contract import Ctx, Item

SITE = "https://example.com"

_LINK = re.compile(r'<a[^>]+href="(/news/[^"#?]+)"[^>]*>([^<]+)</a>')
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def crawl(ctx: Ctx) -> Iterable[Item]:
    index = ctx.fetch(f"{SITE}/news")
    for path, title in _LINK.findall(index):
        url = f"{SITE}{path}"
        body = _WS.sub(" ", _TAGS.sub(" ", ctx.fetch(url))).strip()
        yield Item(url=url, title=title.strip(), body=body, source="example")
