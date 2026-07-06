"""Tests for the position-tracker web UI and its ``jobcrawl serve`` entrypoint (§16).

The app is a thin HTTP surface over the shared §4.7 store: browse/search/filter, move a
posting through the personal pipeline, take notes, dismiss noise, and fire the daemon's
crawl/rescore seams — never a scheduler or an LLM of its own. These tests drive it in-loop
via ``httpx.ASGITransport`` against a ``create_app(store)`` built over one injected
in-memory store (the DI seam the docstring calls out), asserting the JSON contract, the
form→redirect mutations, and that the store's tracking axis is what actually moves.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from jobcrawler.state import PositionRecord, StateStore
from jobcrawler.web import create_app
from jobcrawler.web.app import decode_pid, encode_pid

FIELDS: dict[str, object] = {
    "title": "Staff Engineer",
    "company": "Acme",
    "location": "Remote",
}


def _seeded_store() -> StateStore:
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        "https://acme.test/a",
        fields={
            **FIELDS,
            "workplace": "remote",
            "level": "staff",
            "salary_min": 180000,
            "salary_max": 220000,
            "salary_currency": "USD",
            "description": "Build distributed systems in Rust.",
        },
        content_hash="h1",
    )
    s.set_match(
        "acme",
        "https://acme.test/a",
        score=91,
        verdict="strong",
        reasons=json.dumps(["distributed systems", "rust"]),
    )
    s.upsert_position(
        "beta",
        "https://beta.test/b",
        fields={**FIELDS, "title": "Frontend Engineer"},
        content_hash="h2",
    )
    return s


def _client(app: FastAPI) -> httpx.AsyncClient:
    # follow_redirects stays False so we can assert the 303 form→redirect contract directly.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def _get(store: StateStore, url: str = "https://acme.test/a") -> PositionRecord:
    """Fetch a seeded position that must exist, so callers can read attrs without None-guards."""
    p = store.get_position("acme", url)
    assert p is not None
    return p


_ACME_PID = encode_pid("acme", "https://acme.test/a")


# -- identity encoding -------------------------------------------------------


def test_pid_roundtrip() -> None:
    for site, url in [("acme", "https://acme.test/a?x=1&y=2"), ("s", "u")]:
        assert decode_pid(encode_pid(site, url)) == (site, url)


# -- HTML pages render -------------------------------------------------------


async def test_html_pages_render() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            for path in ("/", "/positions", f"/positions/{_ACME_PID}"):
                r = await c.get(path)
                assert r.status_code == 200, path
                assert "text/html" in r.headers["content-type"]
            # the detail page shows the posting and its match rationale
            r = await c.get(f"/positions/{_ACME_PID}")
            assert "Staff Engineer" in r.text
            assert "distributed systems" in r.text
    finally:
        store.close()


async def test_health() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.get("/health")
            assert r.status_code == 200 and r.json() == {"ok": True}
    finally:
        store.close()


async def test_bad_pid_is_404() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            assert (await c.get("/positions/not-a-real-pid")).status_code == 404
            # well-formed pid for a position that doesn't exist -> 404 too
            missing = encode_pid("acme", "https://acme.test/gone")
            assert (await c.get(f"/positions/{missing}")).status_code == 404
    finally:
        store.close()


# -- JSON: browse / filter ---------------------------------------------------


async def test_api_positions_list_and_filters() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            rows = (await c.get("/api/positions")).json()
            assert {r["site"] for r in rows} == {"acme", "beta"}
            # substring search over title/company/location/description
            rust = (await c.get("/api/positions?q=rust")).json()
            assert [r["url"] for r in rust] == ["https://acme.test/a"]
            # min_score drops the unscored beta row
            scored = (await c.get("/api/positions?min_score=90")).json()
            assert [r["site"] for r in scored] == ["acme"]
            # the scored row carries the decoded reasons list + track label
            assert scored[0]["match_reasons"] == ["distributed systems", "rust"]
            assert scored[0]["track_label"] == "New"
    finally:
        store.close()


async def test_api_dismissed_hidden_by_default() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            await c.post(
                "/api/positions/dismiss",
                json={"site": "acme", "url": "https://acme.test/a"},
            )
            default = (await c.get("/api/positions")).json()
            assert [r["site"] for r in default] == ["beta"]  # acme hidden
            shown = (await c.get("/api/positions?show_dismissed=1")).json()
            assert len(shown) == 2
    finally:
        store.close()


async def test_api_single_position() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.get(f"/api/positions/{_ACME_PID}")
            assert r.status_code == 200
            body = r.json()
            assert body["pid"] == _ACME_PID
            assert body["salary"] == "USD 180,000–220,000"
    finally:
        store.close()


# -- JSON: mutations move the store's tracking axis --------------------------


async def test_api_track_stamps_applied() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                "/api/positions/track",
                json={
                    "site": "acme",
                    "url": "https://acme.test/a",
                    "track_status": "applied",
                },
            )
            assert r.status_code == 200
            body = r.json()
            assert body["track_status"] == "applied"
            assert body["track_label"] == "Applied"
            assert (
                body["applied_at"] is not None
            )  # applied-implying stage stamps the date
            # and the store itself reflects it
            assert _get(store).track_status == "applied"
    finally:
        store.close()


async def test_api_track_rejects_bad_value() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                "/api/positions/track",
                json={
                    "site": "acme",
                    "url": "https://acme.test/a",
                    "track_status": "bogus",
                },
            )
            assert r.status_code == 400
            # nothing was written
            assert _get(store).track_status == "new"
    finally:
        store.close()


async def test_api_track_missing_position_404() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                "/api/positions/track",
                json={
                    "site": "acme",
                    "url": "https://acme.test/gone",
                    "track_status": "applied",
                },
            )
            assert r.status_code == 404
    finally:
        store.close()


async def test_api_notes_and_dismiss() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                "/api/positions/notes",
                json={
                    "site": "acme",
                    "url": "https://acme.test/a",
                    "notes": "referred by dana",
                },
            )
            assert r.status_code == 200 and r.json()["notes"] == "referred by dana"
            r = await c.post(
                "/api/positions/dismiss",
                json={"site": "acme", "url": "https://acme.test/a", "dismissed": True},
            )
            assert r.json()["dismissed"] is True
            assert _get(store).dismissed is True
    finally:
        store.close()


# -- JSON: daemon seams (crawl / rescore) ------------------------------------


async def test_api_rescore_requeues_open_rows() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post("/api/rescore")
            assert r.status_code == 202
            assert r.json() == {"requeued": 2}  # both open rows parked for the daemon
            # the store now has the pending flags set
            assert len(store.pending_matches(limit=10)) == 2
    finally:
        store.close()


async def test_api_crawl_queues_for_daemon() -> None:
    store = _seeded_store()
    try:
        # no registry -> every slug is treated as known (the daemon adjudicates)
        async with _client(create_app(store)) as c:
            r = await c.post("/api/crawl", json={"slug": "acme"})
            assert r.status_code == 202 and r.json() == {
                "queued": "acme",
                "known": True,
            }
        assert store.take_crawl_requests() == ["acme"]
    finally:
        store.close()


async def test_api_crawl_unknown_slug_with_registry() -> None:
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme Corp", name="acme", enabled=True)
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            known = await c.post("/api/crawl", json={"slug": "acme"})
            assert known.json()["known"] is True
            unknown = await c.post("/api/crawl", json={"slug": "ghost"})
            # still queued (202) but flagged unknown so the UI can warn
            assert unknown.status_code == 202 and unknown.json()["known"] is False
    finally:
        store.close()


# -- HTML: form mutations redirect (303) -------------------------------------


async def test_form_status_redirects_and_writes() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                f"/positions/{_ACME_PID}/status", data={"track_status": "interviewing"}
            )
            assert r.status_code == 303
            assert "msg=" in r.headers["location"]
            assert _get(store).track_status == "interviewing"
    finally:
        store.close()


async def test_form_status_bad_value_is_400() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                f"/positions/{_ACME_PID}/status", data={"track_status": "nope"}
            )
            assert r.status_code == 400
    finally:
        store.close()


async def test_form_notes_and_dismiss_redirect() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(
                f"/positions/{_ACME_PID}/notes", data={"notes": "call friday"}
            )
            assert r.status_code == 303
            assert _get(store).notes == "call friday"
            r = await c.post(f"/positions/{_ACME_PID}/dismiss", data={"dismissed": "1"})
            assert r.status_code == 303
            assert _get(store).dismissed is True
    finally:
        store.close()


async def test_dashboard_with_registry_renders_per_site() -> None:
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme Corp", name="acme", enabled=True)
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            r = await c.get("/")
            assert r.status_code == 200
            assert "Acme Corp" in r.text  # per-site panel row
    finally:
        store.close()


# -- CLI: `jobcrawl serve` wiring --------------------------------------------


def test_serve_subcommand_parses() -> None:
    from jobcrawler.cli import build_parser, cmd_serve

    args = build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert args.func is cmd_serve
    assert args.host == "0.0.0.0" and args.port == 9000
    # bare form defers to config defaults (None sentinels)
    bare = build_parser().parse_args(["serve"])
    assert bare.host is None and bare.port is None


def test_serve_app_host_port_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    import uvicorn

    from jobcrawler.web import serve_app

    captured: dict[str, object] = {}

    def fake_run(app: object, *, host: str, port: int, log_level: str = "info") -> None:
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", f"sqlite:///{tmp_path}/serve.db")  # type: ignore[str-bytes-safe]
    monkeypatch.setenv("JOBCRAWLER_SERVE_HOST", "127.0.0.1")
    monkeypatch.setenv("JOBCRAWLER_SERVE_PORT", "8765")

    # explicit CLI args win over env
    assert serve_app(host="0.0.0.0", port=1234) == 0
    assert captured == {"host": "0.0.0.0", "port": 1234}

    # bare form falls back to env/config
    captured.clear()
    assert serve_app() == 0
    assert captured == {"host": "127.0.0.1", "port": 8765}
