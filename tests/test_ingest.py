"""Tests for the POST /news ingest client (§4.7).

Each item is POSTed on its own; the outcome is failure-class-aware so the
caller knows whether to re-attempt (pre-insert failure: nothing was created)
or treat it as terminal-for-cycle (after-send / 5xx: a row + LLM spend may
already exist, so re-POST would duplicate spend). Driven by MockTransport.
"""

from __future__ import annotations

import json

import httpx

from bizup_autocrawler.ingest import IngestOutcome, NewsClient


def _client(handler: object) -> NewsClient:
    c = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return NewsClient(base_url="http://news.test", client=c)


def test_posts_title_and_body_to_news_endpoint() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(201)

    assert _client(handler).post_item("T", "B") is IngestOutcome.INGESTED
    assert seen["url"] == "http://news.test/news"
    assert seen["json"] == {"title": "T", "body": "B"}


def test_2xx_is_ingested() -> None:
    out = _client(lambda r: httpx.Response(200)).post_item("T", "B")
    assert out is IngestOutcome.INGESTED


def test_4xx_is_retry_preinsert() -> None:
    out = _client(lambda r: httpx.Response(400)).post_item("T", "B")
    assert out is IngestOutcome.RETRY


def test_5xx_is_terminal_for_cycle() -> None:
    out = _client(lambda r: httpx.Response(503)).post_item("T", "B")
    assert out is IngestOutcome.TERMINAL


def test_connect_error_is_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert _client(handler).post_item("T", "B") is IngestOutcome.RETRY


def test_read_timeout_after_send_is_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    assert _client(handler).post_item("T", "B") is IngestOutcome.TERMINAL
