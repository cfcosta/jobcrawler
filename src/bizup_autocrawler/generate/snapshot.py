"""Snapshot / corpus shaping for the RLM generator (§4.2, §9).

The generator is kept *idiomatic* RLM by handing it a prefetched site snapshot
as the large input it reasons over in-REPL, using host tools only for follow-up
fetches. dspy injects ``corpus`` into the Pyodide REPL as a real variable by
serializing it as a Python literal, and **raises on any non-JSON type** — so the
corpus is an addressable dict of JSON primitives:

    {homepage, robots, sitemap, articles: [{url, title, text}, ...]}

Addressable parts (the model slices what it needs) beat one opaque blob — up to
8x fewer tokens. Pyodide ships no grep/sed, so ``REPL_HELPERS`` provides plain
navigation functions the generator prompt tells the model are available.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bizup_autocrawler.urls import normalize_host, valid_on_domain_url

if TYPE_CHECKING:
    from bizup_autocrawler.runtime.contract import Ctx

_JSON_SCALARS = (str, int, float, bool, type(None))
_LOC_RE = re.compile(r"<loc\b", re.IGNORECASE)


def build_corpus(
    *,
    homepage: str,
    robots: str = "",
    sitemap: str = "",
    sitemaps: Iterable[str] | None = None,
    expected_recent: int = 0,
    articles: Iterable[Mapping[str, object]] | None = None,
    feeds: Iterable[str] | None = None,
) -> dict[str, object]:
    """Assemble the prefetched snapshot as an addressable, JSON-able dict (§9).

    ``feeds`` carries the discovered RSS/Atom/JSON feed URLs — the feed-first
    signal the strategy reasons over — so the model needn't rediscover them.
    ``sitemaps`` are the sitemap URLs advertised in robots (the news sitemap first)
    and ``expected_recent`` is the host's estimate of how many recent articles the
    site publishes (from that sitemap) — the enumeration target the generator
    reasons toward so it doesn't settle for a feed-sized sample (§4.2, §6).
    """
    corpus: dict[str, object] = {
        "homepage": homepage,
        "robots": robots,
        "sitemap": sitemap,
        "sitemaps": [str(s) for s in (sitemaps or [])],
        "expected_recent": int(expected_recent),
        "articles": [_article(a) for a in (articles or [])],
        "feeds": [str(f) for f in (feeds or [])],
    }
    ensure_json_primitive(corpus)
    return corpus


def sitemaps_from_robots(robots: str) -> list[str]:
    """The ``Sitemap:`` URLs advertised in robots.txt (§4.2 enumeration discovery)."""
    out: list[str] = []
    for line in robots.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "sitemap" and value.strip():
            out.append(value.strip())
    return out


def pick_news_sitemap(sitemaps: list[str]) -> str | None:
    """Prefer a *news* sitemap — it enumerates recent articles, the count we want."""
    for url in sitemaps:
        if "news" in url.lower():
            return url
    return sitemaps[0] if sitemaps else None


def count_sitemap_urls(sitemap_xml: str) -> int:
    """Count article URL entries in a (news) sitemap; 0 for an index or empty.

    A flat news sitemap lists one ``<loc>`` per recent article — the natural
    estimate of how many articles the site is currently publishing. A
    ``<sitemapindex>`` points to child sitemaps, not articles, so it yields no
    article estimate here (the enumeration gate then stays dormant rather than
    keying off a wrong, tiny count).
    """
    if "<sitemapindex" in sitemap_xml.lower():
        return 0
    return len(_LOC_RE.findall(sitemap_xml))


def _article(article: Mapping[str, object]) -> dict[str, str]:
    return {
        "url": str(article.get("url") or ""),
        "title": str(article.get("title") or ""),
        "text": str(article.get("text") or ""),
    }


def ensure_json_primitive(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is built only of JSON primitives.

    Mirrors dspy's in-sandbox serializer (which raises ``CodeInterpreterError``
    on a non-JSON type), so a bad corpus fails here with a clear message rather
    than deep inside the RLM REPL bridge.
    """
    if isinstance(value, _JSON_SCALARS):
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"corpus dict key must be str, got {type(k).__name__}")
            ensure_json_primitive(v)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            ensure_json_primitive(item)
        return
    raise ValueError(f"corpus holds a non-JSON type: {type(value).__name__}")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(v for k, v in attrs if k == "href" and v)


def on_domain_links(html: str, base_url: str) -> list[str]:
    """On-domain absolute article-ish links from ``html`` (deduped, base excluded)."""
    allowed = {normalize_host(base_url)}
    parser = _LinkParser()
    parser.feed(html)
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        url = urljoin(base_url, href)
        if url != base_url and url not in seen and valid_on_domain_url(url, allowed):
            seen.add(url)
            out.append(url)
    return out


def prefetch_corpus(
    site_url: str, ctx: Ctx, *, max_articles: int = 5
) -> dict[str, object]:
    """Fetch a site's snapshot through ``ctx`` into the addressable corpus (§4.2).

    The homepage drives discovery; robots/sitemap are best-effort (missing ones
    become empty). A few on-domain links are pulled as article samples. All
    fetching goes through ``ctx`` so SSRF + politeness apply.
    """
    from bizup_autocrawler.generate.tools import find_feeds_in  # avoid an import cycle

    homepage = ctx.fetch(site_url)
    robots = _try_fetch(ctx, urljoin(site_url, "/robots.txt"))
    # Discover the news sitemap from robots (it enumerates recent articles); fall
    # back to /sitemap.xml when robots advertises none, so the count is best-effort.
    sitemaps = sitemaps_from_robots(robots)
    sitemap_url = pick_news_sitemap(sitemaps) or urljoin(site_url, "/sitemap.xml")
    sitemap = _try_fetch(ctx, sitemap_url)
    links = on_domain_links(homepage, site_url)[:max_articles]
    return build_corpus(
        homepage=homepage,
        robots=robots,
        sitemap=sitemap,
        sitemaps=sitemaps or ([sitemap_url] if sitemap else []),
        expected_recent=count_sitemap_urls(sitemap),
        articles=[
            {"url": url, "title": "", "text": _try_fetch(ctx, url)} for url in links
        ],
        feeds=find_feeds_in(homepage, site_url),  # feed-first discovery (§4.2)
    )


def _try_fetch(ctx: Ctx, url: str) -> str:
    try:
        return ctx.fetch(url)
    except Exception:
        return ""  # robots/sitemap/article fetch is best-effort


# Navigation helpers the generator prompt advertises (Pyodide lacks grep/sed).
# Plain Python so the model can call them on corpus parts inside the REPL.
REPL_HELPERS = '''
def grep(pattern, text, *, flags=0):
    """Return lines of `text` matching the regex `pattern`."""
    import re
    rx = re.compile(pattern, flags)
    return [line for line in text.splitlines() if rx.search(line)]


def slice_text(text, start, end):
    """Return `text[start:end]` — a window into a large corpus part."""
    return text[start:end]
'''
