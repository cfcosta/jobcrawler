"""Tests for §4.4 subprocess isolation of *committed* crawls (``collect_crawler``).

Committed crawlers run on the production schedule, so a runaway one (infinite
loop, memory blowup) must not take down the daemon. ``collect_crawler`` runs them
in the same isolated child as ``validate_crawler`` (AST gate → rlimits → wall-clock
timeout) but emits **full** items — not the 5 bounded samples the scorer needs —
so the host can dedup + ingest them. These spawn the real child process; the test
crawlers yield static items (no network) so the subprocess boundary, full-item
serialization, and the resource bounds are exercised without live traffic.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bizup_autocrawler.runtime.contract import Item
from bizup_autocrawler.runtime.sandbox import collect_crawler

UTC = timezone.utc

_STATIC = """
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    for i in range(8):
        yield Item(url=f"{SITE}/a{i}", title=f"T{i}", body="x" * 5000, source="example")
"""


def test_collect_returns_full_items_not_bounded_samples() -> None:
    # The scorer caps at 5 samples / 600-char bodies; a crawl needs every item in
    # full so the host can ingest them. 8 items come back, bodies untruncated.
    result = collect_crawler(_STATIC, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is None
    assert result.errors == 0
    assert len(result.items) == 8
    assert all(isinstance(it, Item) for it in result.items)
    assert len(result.items[0].body) == 5000  # NOT truncated to 600
    assert {it.title for it in result.items} == {f"T{i}" for i in range(8)}


def test_collect_preserves_published_at_datetime() -> None:
    # published_at round-trips through the JSON boundary back to a tz-aware datetime
    # (the host's first-crawl freshness sort depends on it).
    src = """
from datetime import datetime, timezone
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    yield Item(url=SITE + "/a", title="A", body="x" * 500, source="s",
               published_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
"""
    result = collect_crawler(src, allowed_hosts={"example.com"}, timeout=20.0)
    assert result.exception is None
    assert result.items[0].published_at == datetime(2026, 1, 2, tzinfo=UTC)


def test_collect_isolates_a_crash_keeping_partial_items() -> None:
    # A crawler that yields then raises: the items collected before the raise survive
    # (mirrors the in-process _collect) and the crash is captured, never propagated.
    src = """
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    yield Item(url=SITE + "/a", title="A", body="x" * 500, source="s")
    raise RuntimeError("boom mid-crawl")
    yield None
"""
    result = collect_crawler(src, allowed_hosts={"example.com"}, timeout=20.0)
    assert len(result.items) == 1  # the one yielded before the raise
    assert result.exception is not None
    assert result.exception.type == "RuntimeError"
    assert "boom mid-crawl" in result.exception.message


def test_collect_counts_schema_invalid_yields_without_dropping_valid() -> None:
    src = """
from bizup_autocrawler.runtime.contract import Item
SITE = "https://example.com"
def crawl(ctx):
    yield Item(url=SITE + "/ok", title="OK", body="x" * 500, source="s")
    yield {"url": "", "title": "bad", "body": "no url"}  # invalid: empty url
"""
    result = collect_crawler(src, allowed_hosts={"example.com"}, timeout=20.0)
    assert len(result.items) == 1
    assert result.items[0].title == "OK"
    assert result.errors == 1  # the contract-violating yield


def test_collect_ast_gate_blocks_banned_code_before_running() -> None:
    banned = "import os\nSITE='https://x.com'\ndef crawl(ctx):\n    yield 1\n"
    result = collect_crawler(banned, allowed_hosts={"x.com"}, timeout=20.0)
    assert result.items == []
    assert result.exception is not None
    assert result.exception.type == "ASTGateError"


def test_collect_wall_clock_timeout_hard_stops_a_hang() -> None:
    # The release-safety case: a committed crawler that spins forever must be killed
    # by the wall-clock bound and reported, not hang the daemon thread.
    hang = "SITE='https://x.com'\ndef crawl(ctx):\n    while True:\n        pass\n    yield None\n"
    result = collect_crawler(hang, allowed_hosts={"x.com"}, timeout=1.0)
    assert result.items == []
    assert result.exception is not None
    assert result.exception.type == "TimeoutError"
