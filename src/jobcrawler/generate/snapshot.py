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

import json
import re
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from bizup_autocrawler import config
from bizup_autocrawler.urls import normalize_host, valid_on_domain_url

if TYPE_CHECKING:
    from bizup_autocrawler.runtime.contract import Ctx

_JSON_SCALARS = (str, int, float, bool, type(None))
_LOC_RE = re.compile(r"<loc\b", re.IGNORECASE)
# A link whose href is the WordPress REST base (…/wp-json/) — the api.w.org rel
# link declares it; failing that, any /wp-json/ href on the page does.
_WP_JSON_HREF = re.compile(r'href=["\']([^"\']*/wp-json/)["\']', re.IGNORECASE)
# Path segments that are never an article (nav, taxonomy, WP plumbing) — excluded
# from the article samples so the generator isn't shown tag/category pages.
_NON_ARTICLE = re.compile(
    r"/(tag|category|categoria|topico|topics?|author|autor|page|feed|wp-json|"
    r"wp-admin|wp-content|wp-includes|comments?|search|busca|newsletter|assine|"
    r"sobre|about|contato|contact|privacidade|termos)(/|$|\?)",
    re.IGNORECASE,
)


def build_corpus(
    *,
    homepage: str,
    robots: str = "",
    sitemap: str = "",
    sitemaps: Iterable[str] | None = None,
    expected_recent: int = 0,
    articles: Iterable[Mapping[str, object]] | None = None,
    feeds: Iterable[str] | None = None,
    apis: Iterable[str] | None = None,
) -> dict[str, object]:
    """Assemble the prefetched snapshot as an addressable, JSON-able dict (§9).

    ``feeds`` carries the discovered RSS/Atom/JSON feed URLs — the feed-first
    signal the strategy reasons over — so the model needn't rediscover them.
    ``apis`` carries discovered structured content APIs (e.g. the WordPress REST
    ``/wp-json/wp/v2/posts``) that return *full* article bodies as paginated JSON —
    the best source when present. ``sitemaps`` are the sitemap URLs advertised in
    robots (the news sitemap first) and ``expected_recent`` is the host's estimate
    of how many recent articles the site publishes — the enumeration target the
    generator reasons toward so it doesn't settle for a feed-sized sample (§4.2, §6).
    """
    corpus: dict[str, object] = {
        "homepage": homepage,
        "robots": robots,
        "sitemap": sitemap,
        "sitemaps": [str(s) for s in (sitemaps or [])],
        "expected_recent": int(expected_recent),
        "apis": [str(a) for a in (apis or [])],
        "articles": [_article(a) for a in (articles or [])],
        "feeds": [str(f) for f in (feeds or [])],
    }
    ensure_json_primitive(corpus)
    return corpus


def wordpress_posts_api(homepage: str, base_url: str) -> str | None:
    """The WordPress REST posts endpoint if ``homepage`` is WordPress, else None.

    WordPress exposes every post — with the **full** ``content.rendered`` body — as
    paginated JSON at ``/wp-json/wp/v2/posts?per_page=100``, a far better source
    than scraping or a 10-item RSS. Detected from the ``…/wp-json/`` link the page
    advertises, or failing that the ``/wp-content/`` asset path / generator meta.
    """
    match = _WP_JSON_HREF.search(homepage)
    if match:
        wp_json = urljoin(base_url, match.group(1))
    elif _looks_like_wordpress(homepage):
        wp_json = urljoin(base_url, "/wp-json/")
    else:
        return None
    return urljoin(wp_json, "wp/v2/posts")


def _looks_like_wordpress(homepage: str) -> bool:
    low = homepage.lower()
    return "/wp-content/" in low or "/wp-json/" in low or 'content="wordpress' in low


def _looks_like_article(url: str) -> bool:
    """A homepage link that plausibly points at an article (not nav/taxonomy).

    Excludes WP/taxonomy plumbing, then keeps only links whose final path segment
    reads like an article *slug* — multi-word/hyphenated, dated, or long — so a bare
    section landing page (``/economia/``, ``/play/``) is not sampled as an article.
    """
    if _NON_ARTICLE.search(url):
        return False
    slug = urlparse(url).path.strip("/").rsplit("/", 1)[-1]
    if not slug:
        return False
    return "-" in slug or len(slug) > 20 or any(ch.isdigit() for ch in slug)


def _wp_article_samples(
    ctx: Ctx, posts_api: str, count: int, clip_chars: int
) -> list[dict[str, str]]:
    """Real article samples straight from the WP REST API — full ``content.rendered``
    bodies, so the generator sees genuine articles (not guessed homepage links) and
    that the API itself works."""
    raw = _try_fetch(ctx, f"{posts_api}?per_page={count}&_fields=link,title,content")
    try:
        posts = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[dict[str, str]] = []
    for post in posts if isinstance(posts, list) else []:
        if not isinstance(post, Mapping):
            continue
        title = post.get("title")
        content = post.get("content")
        text = content.get("rendered") if isinstance(content, Mapping) else ""
        if str(post.get("link") or "") and str(text or ""):
            out.append(
                {
                    "url": str(post.get("link")),
                    "title": str(title.get("rendered") or "")
                    if isinstance(title, Mapping)
                    else "",
                    "text": _clip(str(text), clip_chars),
                }
            )
    return out


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
    site_url: str,
    ctx: Ctx,
    *,
    max_articles: int = 5,
    homepage_chars: int | None = None,
    article_chars: int | None = None,
) -> dict[str, object]:
    """Fetch a site's snapshot through ``ctx`` into the addressable corpus (§4.2).

    The homepage drives discovery; robots/sitemap are best-effort (missing ones
    become empty). A few on-domain links are pulled as article samples. All
    fetching goes through ``ctx`` so SSRF + politeness apply. Fetched HTML is
    **clipped** (§9) — raw pages run to 100s of KB, and an unbounded corpus blows
    past the model's context so a reasoning LM exhausts its tokens on nothing.
    Link discovery uses the *full* homepage, so clipping never costs a sample.
    """
    from bizup_autocrawler.generate.tools import find_feeds_in  # avoid an import cycle

    hp_cap = (
        config.corpus_homepage_chars() if homepage_chars is None else homepage_chars
    )
    art_cap = config.corpus_article_chars() if article_chars is None else article_chars

    homepage = ctx.fetch(site_url)
    robots = _try_fetch(ctx, urljoin(site_url, "/robots.txt"))
    # Discover the news sitemap from robots (it enumerates recent articles); fall
    # back to /sitemap.xml when robots advertises none, so the count is best-effort.
    sitemaps = sitemaps_from_robots(robots)
    sitemap_url = pick_news_sitemap(sitemaps) or urljoin(site_url, "/sitemap.xml")
    sitemap = _try_fetch(ctx, sitemap_url)
    # Article samples: from the WP REST API when present (real articles, full body),
    # else on-domain links that look like article slugs (not tag/category/nav pages,
    # which confuse the generator about where the body lives).
    wp_api = wordpress_posts_api(homepage, site_url)
    if wp_api:
        articles = _wp_article_samples(ctx, wp_api, max_articles, art_cap)
    else:
        links = [
            url
            for url in on_domain_links(homepage, site_url)
            if _looks_like_article(url)
        ][:max_articles]
        articles = [
            {"url": url, "title": "", "text": _clip(_try_fetch(ctx, url), art_cap)}
            for url in links
        ]
    return build_corpus(
        homepage=_clip(homepage, hp_cap),
        robots=robots,
        sitemap=sitemap,
        sitemaps=sitemaps or ([sitemap_url] if sitemap else []),
        expected_recent=count_sitemap_urls(sitemap),
        apis=[wp_api] if wp_api else [],
        articles=articles,
        feeds=find_feeds_in(homepage, site_url),  # feed-first discovery (§4.2)
    )


def _clip(text: str, limit: int) -> str:
    return text if limit <= 0 or len(text) <= limit else text[:limit]


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
