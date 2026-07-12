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
from types import SimpleNamespace

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


async def test_swipe_page_has_deck_scaffolding() -> None:
    """The shell carries every mount the deck JS binds to — the card stack, the
    HUD (progress + session tally + undo), the cleared-deck state, and the
    always-visible key legend for the four core keys."""
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            body = (await c.get("/swipe")).text
            for hook in (
                'id="swipe-deck"',
                'id="swipe-hud"',
                'id="swipe-empty"',
                'id="swipe-undo"',
                'id="swipe-bg"',  # WebGL backdrop canvas (fails closed to CSS)
                'id="swipe-help"',  # keybinding overlay (? toggles)
                'data-queue-url="/api/swipe/queue"',
                'data-decide-url="/api/swipe"',
            ):
                assert hook in body, hook
            for key in ("<kbd>j</kbd>", "<kbd>k</kbd>", "<kbd>h</kbd>", "<kbd>l</kbd>"):
                assert key in body, key
    finally:
        store.close()


async def test_swipe_filter_drawer_mirrors_positions_grammar() -> None:
    """The drawer is the /positions filter form aimed back at /swipe: it lists the
    site registry, echoes the live query into its controls (so a deep link and the
    drawer agree), and submits as a plain GET so filtering works without JS."""
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme", name="Acme board"),
        SimpleNamespace(slug="beta", company="Beta", name="Beta board"),
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            body = (
                await c.get("/swipe?q=rust&site=acme&min_score=80&track_status=new")
            ).text
            assert 'id="swipe-drawer"' in body
            assert 'id="swipe-config"' in body  # the HUD toggle button
            assert 'action="/swipe"' in body  # no-JS fallback: plain GET reload
            assert 'value="rust"' in body  # q echo
            assert 'value="80"' in body  # min_score echo
            assert '<option value="acme" selected>' in body
            assert '<option value="beta"' in body  # registry drives the dropdown
            # the same refine axes the list page exposes travel here too
            for field in (
                'name="workplace"',
                'name="country"',
                'name="remote_in"',
                'name="level"',
                'name="min_salary"',
                'name="remote_anywhere"',
                'name="show_dismissed"',
                'name="hide_applied_companies"',
            ):
                assert field in body, field
    finally:
        store.close()


async def test_swipe_bare_defaults_open_on_the_reachable_set() -> None:
    """A bare /swipe opens tighter than /positions: remote-anywhere (home-relative,
    so +BR) OR non-US relocation, stage new, applied companies hidden. The drawer
    echoes the active defaults so the deck's query is visible + shareable."""
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            bare = (await c.get("/swipe")).text
            for checked in (
                'name="remote_anywhere" value="1" checked',
                'name="relocation_no_us" value="1" checked',
                'name="hide_applied_companies" value="1" checked',
            ):
                assert checked in bare, checked
            # an explicit query is taken verbatim — the defaults never re-assert, so a
            # mobility box the operator left unchecked stays unchecked
            explicit = (await c.get("/swipe?track_status=new&sort=score")).text
            assert 'name="remote_anywhere" value="1" checked' not in explicit
            assert 'name="relocation_no_us" value="1" checked' not in explicit
    finally:
        store.close()


async def test_swipe_nav_link_in_shell() -> None:
    """The shared shell nav links the deck from every page (and marks it active)."""
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            for path in ("/positions", "/swipe"):
                assert 'href="/swipe"' in (await c.get(path)).text, path
    finally:
        store.close()


# -- the queue ---------------------------------------------------------------


async def test_swipe_queue_defaults_to_new_undismissed() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            cards, body = await _queue(c)
            pids = _pids(cards)
            # the bare deck opens on the operator's reachable set (§16): the strong
            # remote-anywhere row survives; BETA (Lisbon, office-bound, no relocation)
            # is filtered out by the remote-anywhere / non-US-relocation default
            assert pids == [_ACME_PID]
            assert _BETA_PID not in pids
            # dismissed and already-triaged rows stay out of the default deck too
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
            # the deck's `A` key gates its badge + "already applied" guard on this
            assert card["track_status"] == "new"
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
                # remote-anywhere so the rows clear the bare deck's mobility default
                fields={
                    "title": f"Engineer {i}",
                    "company": "Acme",
                    "location": "Remote",
                    "remote_scope": "anywhere",
                },
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


async def test_swipe_mark_applied_stamps_and_leaves_the_deck() -> None:
    """The deck's capital-``A`` key posts the applied stage to the shared track API:
    it stamps ``applied_at`` and the row drops out of the default ``new`` queue."""
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            assert _ACME_PID in _pids((await _queue(c))[0])  # in the deck to start
            r = await c.post(
                "/api/positions/track",
                json={"site": "acme", "url": _ACME_URL, "track_status": "applied"},
            )
            assert r.status_code == 200
            assert r.json()["track_status"] == "applied"
            rec = store.get_position("acme", _ACME_URL)
            assert rec is not None and rec.track_status == "applied"
            # applied is an application-implying stage, so applied_at is stamped
            assert rec.applied_at is not None
            # and the applied row is no longer stage `new` — it leaves the deck
            assert _ACME_PID not in _pids((await _queue(c))[0])
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
