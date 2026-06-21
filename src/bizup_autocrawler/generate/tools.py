"""Host tools for the RLM generator (§4.2).

The tools run on the *host* (the Pyodide sandbox has no network) and handle
follow-up fetches the prefetched corpus doesn't cover. This module holds the
pure, parser-side tool logic; the network/sandbox/Playwright bindings
(``fetch``/``render``/``get_sitemap``/``validate_crawler``) attach in the §4.4
sandbox + §8 hardening work, since feed/sitemap parsing consumes
attacker-influenced markup on the hot path.

``find_feeds_in`` is the discovery primitive: ``<link rel="alternate">`` RSS /
Atom / JSON feeds, resolved to absolute URLs. Feed-first discovery enumerates
articles (a homepage scrape only samples "recent"), so this is what gate-2
must-observe pushes the generator to consult before choosing ``scrape``.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

_FEED_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/json",
        "application/feed+json",
    }
)


class _FeedLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        rel = attr.get("rel", "").lower()
        typ = attr.get("type", "").lower()
        href = attr.get("href")
        if href and "alternate" in rel.split() and typ in _FEED_TYPES:
            self.hrefs.append(href)


def find_feeds_in(html: str, base_url: str) -> list[str]:
    """Discover ``<link rel="alternate">`` feeds in ``html`` as absolute URLs.

    Order-preserving and de-duplicated (a page often declares the same feed twice).
    """
    parser = _FeedLinkParser()
    parser.feed(html)
    out: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        url = urljoin(base_url, href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
