"""Tests for the generator host tools (§4.2)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import httpx

from bizup_autocrawler.generate.tools import find_feeds_in, make_host_tools
from bizup_autocrawler.runtime.ctx import Ctx

_BASE = "https://example.com/news"
Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(handler: Handler, host: str = "x.com") -> Ctx:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return Ctx({host}, client=client)


def test_finds_rss_and_atom_feeds_as_absolute_urls() -> None:
    html = """
    <head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="https://example.com/atom">
    </head>
    """
    assert find_feeds_in(html, _BASE) == [
        "https://example.com/feed.xml",
        "https://example.com/atom",
    ]


def test_finds_json_feed() -> None:
    html = '<link rel="alternate" type="application/feed+json" href="/feed.json">'
    assert find_feeds_in(html, _BASE) == ["https://example.com/feed.json"]


def test_ignores_non_feed_links() -> None:
    html = (
        '<link rel="stylesheet" href="/style.css">'
        '<link rel="icon" href="/favicon.ico">'
        '<link rel="canonical" href="/news">'
    )
    assert find_feeds_in(html, _BASE) == []


def test_dedupes_repeated_feed_declarations() -> None:
    html = (
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    )
    assert find_feeds_in(html, _BASE) == ["https://example.com/feed.xml"]


def test_no_feeds_returns_empty() -> None:
    assert find_feeds_in("<html><body>no feeds here</body></html>", _BASE) == []


# -- host tools handed to dspy.RLM(tools=...) --------------------------------


def test_host_tools_fetch_and_find_feeds() -> None:
    pages = {
        "/": '<link rel="alternate" type="application/rss+xml" href="/feed">',
        "/feed": "<rss/>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(request.url.path, ""))

    fetch, find_feeds, _get_sitemap, _validate = make_host_tools(
        _ctx(handler), {"x.com"}
    )
    assert cast(str, fetch("https://x.com/")).startswith("<link")
    assert find_feeds("https://x.com/") == ["https://x.com/feed"]


def test_host_validate_tool_runs_the_sandbox() -> None:
    ctx = _ctx(lambda r: httpx.Response(404), host="example.com")
    _fetch, _find, _sitemap, validate = make_host_tools(ctx, {"example.com"})
    source = (
        "from bizup_autocrawler.runtime.contract import Item\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        '    yield Item(url=f"{SITE}/a", title="T", body="x"*500, source="s")\n'
    )
    out = cast(dict, validate(source))
    assert out["item_count"] == 1
    assert out["exception"] is None
