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

from collections.abc import Callable, Collection
from html.parser import HTMLParser
from urllib.parse import urljoin

from bizup_autocrawler.runtime.contract import Ctx
from bizup_autocrawler.runtime.sandbox import validate_crawler as _run_in_sandbox

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


def make_host_tools(
    ctx: Ctx, allowed_hosts: Collection[str], *, validate_timeout: float = 10.0
) -> list[Callable[..., object]]:
    """The §4.2 host tools handed to ``dspy.RLM(tools=...)``.

    They run on the host (the Pyodide sandbox has no network or crawler libs):
    ``fetch``/``find_feeds``/``get_sitemap`` cover follow-up fetches the prefetched
    corpus missed, and ``validate_crawler`` is the bridge to the §4.4 execution
    sandbox so the model can write → validate → fix before SUBMIT. Each is a plain
    named callable with a docstring (RLM uses these as the tool descriptions).
    """
    hosts = set(allowed_hosts)

    def fetch(url: str) -> str:
        """Fetch an on-domain URL and return its text."""
        return ctx.fetch(url)

    def find_feeds(url: str) -> list[str]:
        """Return RSS/Atom/JSON feed URLs linked from the page at `url`."""
        return find_feeds_in(ctx.fetch(url), url)

    def get_sitemap(url: str) -> str:
        """Fetch the sitemap XML at `url` (e.g. https://site/sitemap.xml)."""
        return ctx.fetch(url)

    def validate_crawler(source: str) -> dict[str, object]:
        """Run a candidate crawler in the sandbox; returns items found + samples + any error."""
        result = _run_in_sandbox(source, allowed_hosts=hosts, timeout=validate_timeout)
        return {
            "item_count": result.item_count,
            "samples": result.samples,
            "schema_errors": result.schema_errors,
            "exception": None
            if result.exception is None
            else {
                "type": result.exception.type,
                "message": result.exception.message,
            },
        }

    return [fetch, find_feeds, get_sitemap, validate_crawler]
