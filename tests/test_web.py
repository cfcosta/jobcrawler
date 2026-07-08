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
import re
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
            "company_canonical": "acme",
            "workplace": "remote",
            "level": "staff",
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
            for path in ("/", "/positions", "/applications", f"/positions/{_ACME_PID}"):
                r = await c.get(path)
                assert r.status_code == 200, path
                assert "text/html" in r.headers["content-type"]
            # the detail page shows the posting and its match rationale
            r = await c.get(f"/positions/{_ACME_PID}")
            assert "Staff Engineer" in r.text
            assert "distributed systems" in r.text
            # a distinct apply_url surfaces as an Apply link alongside the original posting
            assert "https://apply.acme.test/a" in r.text
            assert "Apply" in r.text
    finally:
        store.close()


async def test_detail_surfaces_full_record() -> None:
    # #60: the detail page dumps every stored column, not just the header summary —
    # the new Match/Classification/Compensation/Provenance panels expose the salary
    # breakdown, the source board + content hash, and the discipline/level axis.
    store = _seeded_store()
    store.upsert_position(
        "acme",
        "https://acme.test/a",
        fields={"discipline": "backend", "department": "Platform"},
        content_hash="h1b",
    )
    try:
        async with _client(create_app(store)) as c:
            r = await c.get(f"/positions/{_ACME_PID}")
            body = r.text
            # the four new full-record panels are present
            for heading in ("Match &amp; scoring", "Classification", "Compensation",
                            "Provenance"):
                assert heading in body, heading
            # compensation is broken out with thousands separators (source USD range)
            assert "180,000" in body and "220,000" in body
            # provenance shows the source board slug + the content-hash label
            assert "(acme)" in body and "Content hash" in body
            # classification surfaces the crawl-normalized discipline
            assert "backend" in body
    finally:
        store.close()


async def test_detail_renders_a_mailto_apply_link_clickably() -> None:
    # HN-style postings apply by email: apply_url is a mailto:, and the UI must render it as
    # a real clickable mail link (not text) — and without target=_blank, which on a mailto
    # opens a dead blank tab instead of the mail client.
    store = StateStore(":memory:")
    url = "https://news.ycombinator.com/item?id=1"
    store.upsert_position(
        "hn", url,
        fields={**FIELDS, "apply_url": "mailto:jobs@acme.com"},
        content_hash="h",
    )
    pid = encode_pid("hn", url)
    try:
        async with _client(create_app(store)) as c:
            body = (await c.get(f"/positions/{pid}")).text
            assert 'href="mailto:jobs@acme.com"' in body
            assert "Email to apply" in body  # mailto-specific label, not "Apply ↗"
            # the Apply control must not carry target=_blank (dead blank tab on a mailto)
            after = body.split('href="mailto:jobs@acme.com"', 1)[1][:40]
            assert "target=" not in after
    finally:
        store.close()


async def test_static_assets_are_cache_busted() -> None:
    # base.html must stamp app.css/app.js with a ?v=<mtime> cache-buster so an edited
    # asset is refetched instead of a browser silently running a stale copy (the bug where
    # the bulk-selection JS never loaded, so "select all" and the batch bar did nothing).
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            body = (await c.get("/positions")).text
            assert re.search(r'/static/app\.css\?v=\d+', body), "app.css is not cache-busted"
            assert re.search(r'/static/app\.js\?v=\d+', body), "app.js is not cache-busted"
    finally:
        store.close()


async def test_company_links_group_by_canonical() -> None:
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            # each company on the list links to its canonical-grouped view (#51)
            r = await c.get("/positions")
            assert 'href="/positions?company_canonical=acme"' in r.text
            # the filter returns only rows sharing that canonical key (beta's is NULL)
            rows = (await c.get("/api/positions?company_canonical=acme")).json()
            assert {row["site"] for row in rows} == {"acme"}
            # an unknown canonical yields nothing
            assert (await c.get("/api/positions?company_canonical=absent")).json() == []
            # landing on the filtered view shows a clearable context chip
            r = await c.get("/positions?company_canonical=acme")
            assert "Company · Acme" in r.text
    finally:
        store.close()


def test_format_salary_appends_usd_hint_for_non_usd() -> None:
    from jobcrawler.web.app import format_salary

    # a non-USD range keeps its source figure AND gains an annualized-USD hint
    brl = SimpleNamespace(
        salary_min=20000.0,
        salary_max=25000.0,
        salary_currency="BRL",
        salary_period="month",
        salary_usd_min=43200.0,
        salary_usd_max=54000.0,
    )
    assert format_salary(brl) == "BRL 20,000–25,000/month (~$43k–$54k/yr)"  # type: ignore[arg-type]

    # a USD posting needs no hint
    usd = SimpleNamespace(
        salary_min=120000.0,
        salary_max=140000.0,
        salary_currency="USD",
        salary_period="year",
        salary_usd_min=120000.0,
        salary_usd_max=140000.0,
    )
    assert format_salary(usd) == "USD 120,000–140,000/year"  # type: ignore[arg-type]


async def test_relocation_filter_and_badge() -> None:
    store = StateStore(":memory:")
    store.upsert_position(
        "acme",
        "https://acme.test/relo",
        fields={**FIELDS, "title": "Relo Role", "relocation": "offered"},
        content_hash="hr",
    )
    store.upsert_position(
        "acme",
        "https://acme.test/silent",
        fields={**FIELDS, "title": "Quiet Role", "relocation": "unspecified"},
        content_hash="hq",
    )
    try:
        async with _client(create_app(store)) as c:
            # the "offers relocation" checkbox is offered as a filter
            r = await c.get("/positions")
            assert 'name="relocation_offered"' in r.text
            # and a row that offers relocation carries the badge
            assert "relocation</span>" in r.text
            # the filter keeps only rows whose posting states relocation support
            rows = (await c.get("/api/positions?relocation_offered=1")).json()
            assert [row["url"] for row in rows] == ["https://acme.test/relo"]
            assert all(row["relocation"] == "offered" for row in rows)
    finally:
        store.close()


# -- Applications page (§16): only rows you've applied to --------------------


async def test_applications_page_lists_only_applied_positions() -> None:
    store = _seeded_store()  # acme (scored) + beta (frontend), neither applied yet
    try:
        async with _client(create_app(store)) as c:
            # Empty state until an application exists.
            r = await c.get("/applications")
            assert r.status_code == 200
            assert "haven't logged any applications" in r.text
            assert "Staff Engineer" not in r.text

            # Apply to acme; it now appears, beta (never applied) does not.
            store.set_track_status(
                "acme", "https://acme.test/a", "applied", stamp_applied=True
            )
            r = await c.get("/applications")
            assert "Staff Engineer" in r.text
            assert "Frontend Engineer" not in r.text
    finally:
        store.close()


async def test_applications_stage_filter_and_funnel() -> None:
    store = _seeded_store()
    try:
        store.set_track_status(
            "acme", "https://acme.test/a", "interviewing", stamp_applied=True
        )
        async with _client(create_app(store)) as c:
            # The funnel always renders the applied pipeline stages, and the total is 1.
            r = await c.get("/applications")
            assert "Interviewing" in r.text and "Applied" in r.text

            # Filtering to a stage with no rows shows the narrow empty state, not onboarding.
            r = await c.get("/applications?stage=applied")
            assert "No applications in the Applied stage" in r.text
            # Filtering to the row's actual stage surfaces it.
            r = await c.get("/applications?stage=interviewing")
            assert "Staff Engineer" in r.text
            # A bogus stage is ignored rather than erroring the page.
            assert (await c.get("/applications?stage=bogus")).status_code == 200
    finally:
        store.close()


async def test_api_positions_applied_filter() -> None:
    store = _seeded_store()
    try:
        store.set_track_status(
            "acme", "https://acme.test/a", "applied", stamp_applied=True
        )
        async with _client(create_app(store)) as c:
            rows = (await c.get("/api/positions?applied=1")).json()
            assert [row["url"] for row in rows] == ["https://acme.test/a"]
            assert all(row["applied_at"] is not None for row in rows)
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


async def test_api_positions_default_sort_is_score() -> None:
    # #17: with no ?sort, the listing uses best-match order (score desc) so the default
    # view leads with the strongest match rather than the newest crawl. The unscored beta
    # row sinks last under the DESC sort.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            default = (await c.get("/api/positions")).json()
            by_score = (await c.get("/api/positions?sort=score")).json()
            assert [r["url"] for r in default] == [r["url"] for r in by_score]
            assert default[0]["site"] == "acme"  # scored 91 leads the unscored row
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
            assert body["apply_url"] == "https://apply.acme.test/a"
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
            # the store now has the pending flags set (both rows, descriptionless or not —
            # the plain drain keeps them; only the rebuild-drain filters descriptionless)
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


async def test_api_recrawl_all_forces_enabled_fleet_and_requeues() -> None:
    # The "re-crawl & rescore everything" trigger (#57) enqueues a forced crawl for every
    # *enabled* registry site and parks all open rows for the match-drain — one write each,
    # never a crawl or LLM call in the web process.
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme Corp", name="acme", enabled=True),
        SimpleNamespace(slug="beta", company="Beta Inc", name="beta", enabled=True),
        SimpleNamespace(slug="ghost", company="Ghost", name="ghost", enabled=False),
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            r = await c.post("/api/recrawl-all")
            assert r.status_code == 202
            # disabled 'ghost' is excluded; both open rows parked
            assert r.json() == {"queued": 2, "requeued": 2}
        # the daemon drains exactly the enabled slugs (ordered by requested_at, slug)
        assert store.take_crawl_requests() == ["acme", "beta"]
        assert len(store.pending_matches(limit=10)) == 2
    finally:
        store.close()


async def test_recrawl_all_html_redirects_with_summary() -> None:
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme Corp", name="acme", enabled=True)
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            r = await c.post("/recrawl-all")
            assert r.status_code == 303
            loc = r.headers["location"]
            assert loc.startswith("/") and "msg=" in loc
        assert store.take_crawl_requests() == ["acme"]
    finally:
        store.close()


async def test_recrawl_all_without_registry_still_rescores() -> None:
    # No registry → nothing to crawl, but the open-row requeue still runs (queued=0).
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.post("/api/recrawl-all")
            assert r.status_code == 202
            assert r.json() == {"queued": 0, "requeued": 2}
        assert store.take_crawl_requests() == []
    finally:
        store.close()


# -- Bulk actions on the positions list (#58) --------------------------------


async def test_bulk_status_dismiss_and_note() -> None:
    store = _seeded_store()
    pid_a = encode_pid("acme", "https://acme.test/a")
    pid_b = encode_pid("beta", "https://beta.test/b")
    try:
        async with _client(create_app(store)) as c:
            # bulk "set stage → applied" across both rows stamps applied_at like the
            # single-row path (implies_applied)
            r = await c.post(
                "/positions/bulk",
                data={
                    "action": "status",
                    "pids": [pid_a, pid_b],
                    "track_status": "applied",
                },
            )
            assert r.status_code == 303 and "msg=" in r.headers["location"]
            assert _get(store).track_status == "applied"
            assert _get(store).applied_at is not None
            beta = store.get_position("beta", "https://beta.test/b")
            assert beta is not None and beta.track_status == "applied"

            # bulk dismiss one row
            await c.post("/positions/bulk", data={"action": "dismiss", "pids": [pid_a]})
            assert _get(store).dismissed is True

            # JSON bulk note appends (not overwrites) and counts hits
            r = await c.post(
                "/api/positions/bulk",
                json={"action": "note", "pids": [pid_a], "note": "ping"},
            )
            assert r.json() == {"action": "note", "updated": 1}
            assert "ping" in (_get(store).notes or "")

            # a bad stage value is a 400 before any write
            bad = await c.post(
                "/api/positions/bulk",
                json={"action": "status", "pids": [pid_a], "track_status": "nope"},
            )
            assert bad.status_code == 400
    finally:
        store.close()


async def test_bulk_edge_cases() -> None:
    store = _seeded_store()
    pid_a = encode_pid("acme", "https://acme.test/a")
    stale = encode_pid("ghost", "https://ghost.test/x")  # never in the store
    try:
        async with _client(create_app(store)) as c:
            # empty selection → 303 with a "nothing selected" flash, no error
            r = await c.post("/positions/bulk", data={"action": "dismiss", "pids": []})
            assert r.status_code == 303 and "No+positions+selected" in r.headers[
                "location"
            ].replace("%20", "+")

            # unknown action → 400
            bad = await c.post(
                "/api/positions/bulk", json={"action": "explode", "pids": [pid_a]}
            )
            assert bad.status_code == 400

            # a stale pid mixed with a live one applies only to the live row
            r = await c.post(
                "/api/positions/bulk",
                json={"action": "dismiss", "pids": [pid_a, stale]},
            )
            assert r.json() == {"action": "dismiss", "updated": 1}
            assert _get(store).dismissed is True

            # note append is newline-joined onto an existing note
            store.set_notes("acme", "https://acme.test/a", "first")
            await c.post(
                "/api/positions/bulk",
                json={"action": "note", "pids": [pid_a], "note": "second"},
            )
            assert (_get(store).notes or "") == "first\nsecond"
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
            # the site name links to that site's filtered position list (#50)
            assert 'href="/positions?site=acme"' in r.text
    finally:
        store.close()


async def test_dashboard_top_matches_hide_applied_positions() -> None:
    # The one scored lead ("Staff Engineer") shows in the dashboard's top matches until
    # you apply to it; once applied it belongs to your pipeline, not the fresh-leads list.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            r = await c.get("/")
            assert "Staff Engineer" in r.text

            r = await c.post(
                f"/positions/{_ACME_PID}/status", data={"track_status": "applied"}
            )
            assert r.status_code == 303
            assert _get(store).applied_at is not None

            r = await c.get("/")
            assert "Staff Engineer" not in r.text  # dropped from top matches
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
