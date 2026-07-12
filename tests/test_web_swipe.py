"""Tests for the swipe-triage surface (§16): ``/swipe`` + its queue/decision API.

The deck is a JS-first "one card at a time" triage over the SAME store seams the
positions list uses: the queue endpoint speaks the exact ``_read_filters`` grammar
(bare query = the operator's working set: stage ``new``, applied companies hidden),
and a decision writes through the existing tracking mutators — ``h``/swipe-left is
``set_dismissed`` and ``l``/swipe-right is the guarded one-way ``new → interested``
promotion (never resurrecting a rejected/withdrawn row). Cards carry a
presentation-ready payload (formatted salary, humanized age, score band, avatar hue,
match reasons/concerns) so the client renders without re-deriving display policy.
"""

from __future__ import annotations

import json

import httpx
from fastapi import FastAPI

from jobcrawler.state import StateStore
from jobcrawler.web import create_app
from jobcrawler.web.app import encode_pid

_ACME_URL = "https://acme.test/a"
_BETA_URL = "https://beta.test/b"
_GAMA_URL = "https://gama.test/c"
_DELTA_URL = "https://delta.test/d"

_ACME_PID = encode_pid("acme", _ACME_URL)
_BETA_PID = encode_pid("beta", _BETA_URL)
_GAMA_PID = encode_pid("gama", _GAMA_URL)
_DELTA_PID = encode_pid("delta", _DELTA_URL)


def _seeded_store() -> StateStore:
    """Four rows spanning the triage axes: two fresh ``new`` rows (one strong, one
    mid), one dismissed, one already-``interested`` — so the default queue has an
    exact expected membership and each exclusion has a row proving it."""
    s = StateStore(":memory:")
    s.upsert_position(
        "acme",
        _ACME_URL,
        fields={
            "title": "Staff Engineer",
            "company": "Acme",
            "company_canonical": "acme",
            "location": "Remote",
            "workplace": "remote",
            "remote_scope": "anywhere",
            "level": "staff_plus",
            "discipline": "backend",
            "salary_min": 180000,
            "salary_max": 220000,
            "salary_currency": "USD",
            "description": "Build distributed systems in Rust.",
            "apply_url": "https://apply.acme.test/a",
        },
        content_hash="h1",
    )
    s.set_match(
        "acme",
        _ACME_URL,
        score=91,
        verdict="strong",
        reasons=json.dumps(["distributed systems", "rust"]),
        concerns=json.dumps(["on-call rotation"]),
    )
    s.upsert_position(
        "beta",
        _BETA_URL,
        fields={"title": "Frontend Engineer", "company": "Beta", "location": "Lisbon"},
        content_hash="h2",
    )
    s.set_match(
        "beta", _BETA_URL, score=55, verdict="stretch", reasons=json.dumps(["react"])
    )
    s.upsert_position(
        "gama",
        _GAMA_URL,
        fields={"title": "Platform Engineer", "company": "Gama", "location": "Remote"},
        content_hash="h3",
    )
    s.set_dismissed("gama", _GAMA_URL, True)
    s.upsert_position(
        "delta",
        _DELTA_URL,
        fields={"title": "Backend Engineer", "company": "Delta", "location": "Berlin"},
        content_hash="h4",
    )
    s.set_track_status("delta", _DELTA_URL, "interested")
    return s


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def _queue(
    c: httpx.AsyncClient, query: str = ""
) -> tuple[list[dict[str, object]], dict[str, object]]:
    r = await c.get(f"/api/swipe/queue{query}")
    assert r.status_code == 200, r.text
    body = r.json()
    return body["positions"], body


def _pids(cards: list[dict[str, object]]) -> list[object]:
    return [card["pid"] for card in cards]


# -- the page ----------------------------------------------------------------


async def test_swipe_page_renders() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.get("/swipe")
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            body = r.text
            # the JS mount + its API config travel on data attributes
            assert 'id="swipe-deck"' in body
            assert "/api/swipe/queue" in body
            assert "/api/swipe" in body
            # a swipe deck is JS-first; no-JS users are pointed at the list
            assert "<noscript>" in body
            assert "/positions" in body
            # filtered deep links must render too (filters echo comes via the drawer)
            r = await c.get("/swipe?min_score=80&site=acme&track_status=new")
            assert r.status_code == 200
    finally:
        store.close()


# -- the queue ---------------------------------------------------------------


async def test_swipe_queue_defaults_to_new_undismissed() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            cards, body = await _queue(c)
            pids = _pids(cards)
            # the two fresh `new` rows, best match first (sort=score default)
            assert pids == [_ACME_PID, _BETA_PID]
            # dismissed and already-triaged rows stay out of the default deck
            assert _GAMA_PID not in pids
            assert _DELTA_PID not in pids
            assert body["page"] == 1
            assert body["has_next"] is False
    finally:
        store.close()


async def test_swipe_queue_card_payload() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            cards, _ = await _queue(c)
            card = cards[0]
            assert card["pid"] == _ACME_PID
            assert card["title"] == "Staff Engineer"
            assert card["company"] == "Acme"
            # presentation-ready extras, so the client renders without policy
            assert card["salary"] == "USD 180,000–220,000"
            assert card["score"] == 91
            assert card["band"] == "strong"
            assert isinstance(card["hue"], int) and 0 <= card["hue"] < 360
            assert isinstance(card["age"], str) and card["age"]
            assert card["ext"] == "https://apply.acme.test/a"
            assert card["detail_url"] == f"/positions/{_ACME_PID}"
            assert card["match_reasons"] == ["distributed systems", "rust"]
            assert card["match_concerns"] == ["on-call rotation"]
            assert card["level_label"] == "Staff+"
            assert card["discipline_label"] == "Backend"
            assert card["workplace_label"] == "Remote"
            assert card["description"] == "Build distributed systems in Rust."
    finally:
        store.close()


async def test_swipe_queue_respects_filters() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            # min_score keeps only the strong row (explicit stage: the drawer
            # always submits its selects, so a non-bare query spells them out)
            cards, _ = await _queue(c, "?track_status=new&min_score=80")
            assert _pids(cards) == [_ACME_PID]
            # free-text search
            cards, _ = await _queue(c, "?track_status=new&q=frontend")
            assert _pids(cards) == [_BETA_PID]
            # site pin
            cards, _ = await _queue(c, "?track_status=new&site=beta")
            assert _pids(cards) == [_BETA_PID]
            # any-stage + show_dismissed opens the whole pile back up
            cards, _ = await _queue(c, "?track_status=&show_dismissed=1")
            pids = _pids(cards)
            assert _GAMA_PID in pids and _DELTA_PID in pids
    finally:
        store.close()


async def test_swipe_queue_paginates() -> None:
    store = StateStore(":memory:")
    try:
        for i in range(55):
            store.upsert_position(
                "acme",
                f"https://acme.test/{i}",
                fields={"title": f"Engineer {i}", "company": "Acme", "location": "R"},
                content_hash=f"h{i}",
            )
        async with _client(create_app(store)) as c:
            cards, body = await _queue(c)
            assert len(cards) == 50
            assert body["has_next"] is True
            cards, body = await _queue(c, "?page=2")
            assert len(cards) == 5
            assert body["has_next"] is False
    finally:
        store.close()


# -- decisions ---------------------------------------------------------------


async def test_swipe_interested_is_guarded_one_way() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(f"/api/swipe/{_ACME_PID}", json={"decision": "interested"})
            assert r.status_code == 200
            body = r.json()
            assert body["changed"] is True
            assert body["track_status"] == "interested"
            rec = store.get_position("acme", _ACME_URL)
            assert rec is not None and rec.track_status == "interested"
            # interested does not imply an application was submitted
            assert rec.applied_at is None

            # idempotent: the row is no longer `new`, so nothing is written
            r = await c.post(f"/api/swipe/{_ACME_PID}", json={"decision": "interested"})
            assert r.status_code == 200
            assert r.json()["changed"] is False

            # and a non-new row (already interested) reports unchanged too
            r = await c.post(
                f"/api/swipe/{_DELTA_PID}", json={"decision": "interested"}
            )
            assert r.json()["changed"] is False
    finally:
        store.close()


async def test_swipe_dismiss() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(f"/api/swipe/{_BETA_PID}", json={"decision": "dismiss"})
            assert r.status_code == 200
            body = r.json()
            assert body["changed"] is True
            assert body["dismissed"] is True
            rec = store.get_position("beta", _BETA_URL)
            assert rec is not None and rec.dismissed is True

            # the dismissed card leaves the default queue
            cards, _ = await _queue(c)
            assert _BETA_PID not in _pids(cards)

            # idempotent re-fire
            r = await c.post(f"/api/swipe/{_BETA_PID}", json={"decision": "dismiss"})
            assert r.json()["changed"] is False
    finally:
        store.close()


async def test_swipe_decision_validation() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post(f"/api/swipe/{_ACME_PID}", json={"decision": "meh"})
            assert r.status_code == 400
            r = await c.post("/api/swipe/not-a-pid!", json={"decision": "dismiss"})
            assert r.status_code == 404
            missing = encode_pid("acme", "https://acme.test/missing")
            r = await c.post(f"/api/swipe/{missing}", json={"decision": "dismiss"})
            assert r.status_code == 404
    finally:
        store.close()
