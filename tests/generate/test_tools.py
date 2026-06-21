"""Tests for the generator host tools (§4.2)."""

from __future__ import annotations

from bizup_autocrawler.generate.tools import find_feeds_in

_BASE = "https://example.com/news"


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
