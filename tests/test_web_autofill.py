"""Tests for the §18 auto-fill web surface: the enqueue-only POST + detail panel.

The web app never opens Chrome or calls an LLM — the button writes an
``autofill_runs`` row for the daemon's drain task, exactly like "crawl now".
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from jobcrawler.state import StateStore
from jobcrawler.web import create_app
from jobcrawler.web.app import encode_pid

FIELDS: dict[str, object] = {
    "title": "Staff Engineer",
    "company": "Acme",
    "location": "Remote",
}

HTTP_URL = "https://acme.test/a"
MAIL_URL = "https://acme.test/mail"


def _seeded_store() -> StateStore:
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        HTTP_URL,
        fields={**FIELDS, "apply_url": "https://apply.acme.test/a"},
        content_hash="h1",
    )
    s.upsert_position(
        "acme",
        MAIL_URL,
        fields={**FIELDS, "apply_url": "mailto:jobs@acme.test"},
        content_hash="h2",
    )
    return s


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_post_autofill_queues_a_run_for_the_daemon() -> None:
    store = _seeded_store()
    pid = encode_pid("acme", HTTP_URL)
    async with _client(create_app(store)) as client:
        resp = await client.post(f"/positions/{pid}/autofill")
        assert resp.status_code == 303
        assert f"/positions/{pid}" in resp.headers["location"]
        # the row is queued with the http(s) apply target; nothing ran
        claimed = store.claim_autofill()
        assert claimed is not None
        assert claimed["site"] == "acme" and claimed["url"] == HTTP_URL
        assert claimed["apply_url"] == "https://apply.acme.test/a"


async def test_post_autofill_is_idempotent_while_pending() -> None:
    store = _seeded_store()
    pid = encode_pid("acme", HTTP_URL)
    async with _client(create_app(store)) as client:
        await client.post(f"/positions/{pid}/autofill")
        await client.post(f"/positions/{pid}/autofill")  # double-press
    assert store.claim_autofill() is not None
    assert store.claim_autofill() is None  # one row, not two


async def test_post_autofill_rejects_a_mailto_only_position() -> None:
    store = _seeded_store()
    pid = encode_pid("acme", MAIL_URL)
    async with _client(create_app(store)) as client:
        resp = await client.post(f"/positions/{pid}/autofill")
        assert resp.status_code == 400
        assert "no http(s) apply link" in resp.json()["detail"]
    assert store.claim_autofill() is None


async def test_detail_page_shows_the_panel_only_for_http_targets() -> None:
    store = _seeded_store()
    app = create_app(store)
    async with _client(app) as client:
        with_link = await client.get(f"/positions/{encode_pid('acme', HTTP_URL)}")
        assert with_link.status_code == 200
        assert "Pre-fill application in Chrome" in with_link.text
        assert "never submits" in with_link.text
        mail_only = await client.get(f"/positions/{encode_pid('acme', MAIL_URL)}")
        assert mail_only.status_code == 200
        assert "Pre-fill application in Chrome" not in mail_only.text


async def test_detail_page_lists_recent_runs() -> None:
    store = _seeded_store()
    rid = store.request_autofill("acme", HTTP_URL, "https://apply.acme.test/a")
    assert store.claim_autofill() is not None
    store.finish_autofill(
        rid, status="done", summary="7 fields filled; review salary", fields_filled=7
    )
    store.request_autofill("acme", HTTP_URL, "https://apply.acme.test/a")  # queued
    async with _client(create_app(store)) as client:
        resp = await client.get(f"/positions/{encode_pid('acme', HTTP_URL)}")
        assert resp.status_code == 200
        assert "7 fields filled; review salary" in resp.text
        assert "queued" in resp.text
