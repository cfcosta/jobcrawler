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
from typing import cast

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
            # unrestricted remote: the dashboard shortlist only seats attainable rows
            "remote_scope": "anywhere",
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
            for path in (
                "/",
                "/positions",
                "/applications",
                "/boards",
                f"/positions/{_ACME_PID}",
            ):
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
            for heading in (
                "Match &amp; scoring",
                "Classification",
                "Compensation",
                "Provenance",
            ):
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
        "hn",
        url,
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


async def test_positions_default_view_is_new_with_applied_companies_hidden() -> None:
    # A bare /positions opens on the working set: stage 'new' only, with "Hide
    # applied companies" pre-checked. Any explicit query — even one that names no
    # stage — disables the defaults, so an unchecked checkbox on a submitted form
    # (absent param, non-empty query) is honored rather than re-defaulted.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            beta_pid = encode_pid("beta", "https://beta.test/b")
            r = await c.post(
                f"/positions/{beta_pid}/status", data={"track_status": "interested"}
            )
            assert r.status_code == 303

            bare = (await c.get("/positions")).text
            assert "Staff Engineer" in bare  # still stage 'new'
            assert "Frontend Engineer" not in bare  # moved out of the default view
            assert 'name="hide_applied_companies" value="1" checked' in bare

            explicit = (await c.get("/positions?sort=score")).text
            assert "Frontend Engineer" in explicit  # defaults not injected
            assert 'name="hide_applied_companies" value="1" checked' not in explicit
    finally:
        store.close()


async def test_saved_views_and_new_since_last_visit() -> None:
    # Quick-view pills render and mark the active one; the "new since last visit" badge
    # is driven by the jc_seen cookie — absent on a first visit, then counting arrivals.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            bare = await c.get("/positions")
            assert 'class="view-pill is-active"' in bare.text  # New view is active
            assert "Strong ≥ 80" in bare.text
            # first visit sets the cookie but shows no badge yet
            assert "since last visit" not in bare.text
            assert c.cookies.get("jc_seen")

            # a second visit with a far-past cookie counts everything as new
            r = await c.get(
                "/positions", headers={"cookie": "jc_seen=2000-01-01T00:00:00+00:00"}
            )
            assert "since last visit" in r.text

            # the strong pill is active on its own query
            strong = await c.get("/positions?min_score=80&sort=score")
            assert strong.text.count('class="view-pill is-active"') == 1
            assert "Strong ≥ 80" in strong.text
    finally:
        store.close()


async def test_rows_link_the_real_position_page_not_the_aggregator() -> None:
    # Aggregator boards (Working Nomads et al.) store their own page as ``url``; the
    # real ATS posting is the captured apply_url behind their "Apply for this
    # position" button. Every row surface must offer it as the row's external ↗ link;
    # a row without one falls back to the original posting, and a mailto apply
    # channel is not a page, so it never becomes the row link.
    store = _seeded_store()
    store.upsert_position(
        "hn",
        "https://news.ycombinator.com/item?id=1",
        fields={**FIELDS, "title": "Email Role", "apply_url": "mailto:jobs@x.com"},
        content_hash="h3",
    )
    try:
        async with _client(create_app(store)) as c:
            listing = (await c.get("/positions")).text
            # acme carries an ATS apply_url → the row ↗ points there, not at acme.test/a
            assert 'class="ext row-ext" href="https://apply.acme.test/a"' in listing
            # beta has no apply_url → falls back to the original posting URL
            assert 'class="ext row-ext" href="https://beta.test/b"' in listing
            # the mailto row's ↗ is the posting page, never the mailto
            assert (
                'class="ext row-ext" href="https://news.ycombinator.com/item?id=1"'
                in listing
            )
            # the dashboard's top-matches list links the ATS page too (acme scored 91)
            dashboard = (await c.get("/")).text
            assert 'class="ext row-ext" href="https://apply.acme.test/a"' in dashboard
    finally:
        store.close()


async def test_effective_score_is_decayed_and_surfaced() -> None:
    # §6: the UI shows the freshness-decayed effective score, with the raw fit alongside.
    from datetime import datetime, timedelta, timezone

    store = StateStore(":memory:")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    store.upsert_position(
        "b", "u", fields={**FIELDS, "posted_at": old}, content_hash="h"
    )
    store.set_match("b", "u", score=90, verdict="strong", reasons="[]")
    pid = encode_pid("b", "u")
    try:
        async with _client(create_app(store)) as c:
            body = (await c.get(f"/api/positions/{pid}")).json()
            assert body["match_score"] == 90  # raw fit unchanged
            assert body["effective_score"] < 90  # freshness-decayed
            assert (
                body["effective_score"] >= 0.6 * 90
            )  # ...but floored (great fit stays)
            # the detail page shows the adjusted score AND the base fit for transparency
            html = (await c.get(f"/positions/{pid}")).text
            assert "base fit 90" in html
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
            assert re.search(r"/static/app\.css\?v=\d+", body), (
                "app.css is not cache-busted"
            )
            assert re.search(r"/static/app\.js\?v=\d+", body), (
                "app.js is not cache-busted"
            )
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
    assert (
        format_salary(cast(PositionRecord, brl))
        == "BRL 20,000–25,000/month (~$43k–$54k/yr)"
    )

    # a USD posting needs no hint
    usd = SimpleNamespace(
        salary_min=120000.0,
        salary_max=140000.0,
        salary_currency="USD",
        salary_period="year",
        salary_usd_min=120000.0,
        salary_usd_max=140000.0,
    )
    assert format_salary(cast(PositionRecord, usd)) == "USD 120,000–140,000/year"


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


async def test_boards_page_renders_per_site_and_dashboard_links_it() -> None:
    # Coverage is ops admin, so it lives on its own /boards page; the dashboard keeps
    # only the one-line health summary linking there.
    store = _seeded_store()
    sites = [
        SimpleNamespace(slug="acme", company="Acme Corp", name="acme", enabled=True)
    ]
    try:
        async with _client(create_app(store, sites=sites)) as c:
            r = await c.get("/boards")
            assert r.status_code == 200
            assert "Acme Corp" in r.text  # per-site row
            # the site name links to that site's filtered position list (#50)
            assert 'href="/positions?site=acme"' in r.text

            dash = (await c.get("/")).text
            assert 'href="/boards"' in dash  # health line links the ops page
            assert 'href="/positions?site=acme"' not in dash  # table moved off
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


# -- CLI: `jobcrawl run` web wiring ------------------------------------------


def test_run_subcommand_parses_web_flags() -> None:
    # The UI now rides `jobcrawl run` (there is no `serve`), with optional bind overrides.
    from jobcrawler.cli import build_parser, cmd_run

    args = build_parser().parse_args(["run", "--host", "0.0.0.0", "--port", "9000"])
    assert args.func is cmd_run
    assert args.host == "0.0.0.0" and args.port == 9000 and args.no_web is False
    bare = build_parser().parse_args(["run"])
    assert bare.host is None and bare.port is None and bare.no_web is False
    assert build_parser().parse_args(["run", "--no-web"]).no_web is True


def test_serve_subcommand_is_gone() -> None:
    # It was removed in favor of the in-process UI; parsing it must fail loudly.
    from jobcrawler.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["serve"])


async def test_crawl_now_button_queues_a_request_the_scheduler_drains() -> None:
    # The shared-store contract behind "crawl now": the UI (mounted in-process on the
    # scheduler's store by `jobcrawl run`) writes request_crawl(); the scheduler's
    # due_sites() drains it and dispatches the site even when its cadence hasn't elapsed.
    from datetime import datetime, timezone

    from jobcrawler.runtime.runner import CrawlSummary
    from jobcrawler.schedule import Scheduler, Site

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = StateStore(":memory:", clock=lambda: now)
    site = Site(slug="acme", url="https://acme.test")
    # a run stamped "now" so the 4h cadence has NOT elapsed → not due on its own
    rid = store.start_run("acme")
    store.finish_run(rid, items_found=1, new_items=0, errors=0, pages_fetched=1)

    async def _crawl(s: Site) -> CrawlSummary:  # never invoked by due_sites()
        raise AssertionError("crawl must not run in this test")

    sched = Scheduler([site], store=store, crawl=_crawl, clock=lambda: now)
    try:
        assert sched.due_sites() == []  # not due; drains the (empty) request queue
        async with _client(create_app(store, sites=[site])) as c:
            assert (await c.post("/sites/acme/crawl")).status_code == 303
        # the button's request is now drained by the scheduler → the site is forced due
        assert [s.slug for s in sched.due_sites()] == ["acme"]
    finally:
        store.close()


# -- §17 cross-board dedup UI -------------------------------------------------


def _cluster_store() -> StateStore:
    """Two boards carrying one greenhouse req (a real T1 cluster) + a solo row."""
    from jobcrawler.runtime.dedup import resweep

    s = StateStore(":memory:")
    s.upsert_position(
        "board_a",
        "https://boards.greenhouse.io/acme/jobs/400123",
        fields={**FIELDS, "description": "short"},
        content_hash="c1",
    )
    s.upsert_position(
        "board_b",
        "https://acme.test/careers?gh_jid=400123",
        fields={**FIELDS, "description": "a much longer description " * 10},
        content_hash="c2",
    )
    s.upsert_position(
        "solo", "https://solo.test/jobs/1", fields=FIELDS, content_hash="c3"
    )
    resweep(s, direct_sites=(), tiers={0, 1, 2, 3, 6})
    return s


async def test_positions_collapse_on_by_default_with_escape() -> None:
    store = _cluster_store()
    try:
        async with _client(create_app(store)) as c:
            collapsed = (await c.get("/positions")).text
            # One row per real-world job: the elected canonical (longest
            # description → board_b) plus the standalone row.
            assert "gh_jid" in collapsed  # board_b's URL is percent-escaped in pids
            assert "boards.greenhouse.io/acme/jobs/400123" not in collapsed
            assert "solo.test/jobs/1" in collapsed
            # The canonical row wears the cluster chip.
            assert "on 2 boards" in collapsed
            # The refine line offers the raw escape…
            assert "Show duplicates" in collapsed
            # …and it works: every copy, chips on both cluster members.
            raw = (await c.get("/positions?collapse=0")).text
            assert "boards.greenhouse.io/acme/jobs/400123" in raw
            # ">on 2 boards<" is the chip's visible text (its title attribute
            # repeats the phrase); one chip per cluster member.
            assert raw.count(">on 2 boards<") == 2
    finally:
        store.close()


async def test_detail_lists_cluster_siblings() -> None:
    store = _cluster_store()
    try:
        pid = encode_pid("board_a", "https://boards.greenhouse.io/acme/jobs/400123")
        async with _client(create_app(store)) as c:
            html = (await c.get(f"/positions/{pid}")).text
            assert "Also listed on 1 other board" in html
            assert encode_pid("board_b", "https://acme.test/careers?gh_jid=400123") in html
            # The sibling is the elected canonical — the copy the list shows.
            assert "shown in list" in html
            # The standalone row renders no siblings panel at all.
            solo = encode_pid("solo", "https://solo.test/jobs/1")
            assert "Also listed" not in (await c.get(f"/positions/{solo}")).text
    finally:
        store.close()


async def test_dashboard_shows_distinct_open_kpi() -> None:
    store = _cluster_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/")).text
            # 3 open rows, 2 distinct real-world jobs.
            assert "Distinct open roles" in html
    finally:
        store.close()


async def test_dedup_ui_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Flipping the env off must revert the whole UI to raw rows per render —
    # no app rebuild, no residue: config getters read the env at call time.
    store = _cluster_store()
    monkeypatch.setenv("JOBCRAWLER_DEDUP_UI", "0")
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/positions")).text
            assert "boards.greenhouse.io/acme/jobs/400123" in html  # nothing hidden
            assert "Show duplicates" not in html
            assert "on 2 boards" not in html
            pid = encode_pid(
                "board_a", "https://boards.greenhouse.io/acme/jobs/400123"
            )
            assert "Also listed" not in (await c.get(f"/positions/{pid}")).text
            assert "Distinct open roles" not in (await c.get("/")).text
    finally:
        store.close()


# -- §17 role groups: same role, several locations ------------------------------


def _role_group_store() -> StateStore:
    """One role open in two cities (a §17 role group) + an unrelated solo row."""
    from jobcrawler.runtime.dedup import resweep

    s = StateStore(":memory:")
    # relocation-offered so the dashboard shortlist (attainable rows only) seats them
    base = {**FIELDS, "relocation": "offered"}
    s.upsert_position(
        "board_a",
        "https://acme.test/jobs/ny",
        fields={**base, "location": "New York, NY"},
        content_hash="r1",
    )
    s.upsert_position(
        "board_b",
        "https://acme.test/jobs/london",
        fields={**base, "location": "London"},
        content_hash="r2",
    )
    s.upsert_position(
        "solo",
        "https://solo.test/jobs/1",
        fields={**base, "title": "Data Engineer", "location": "Berlin"},
        content_hash="r3",
    )
    # Only the London variant carries a verdict → it is the group's primary.
    s.set_match(
        "board_b", "https://acme.test/jobs/london", score=85, verdict="strong",
        reasons="[]",
    )
    resweep(s, direct_sites=(), tiers={0, 1, 2, 3, 6})
    return s


async def test_positions_role_group_collapses_to_one_row_with_picker() -> None:
    store = _role_group_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/positions")).text
            # One row for the two-city role (the scored London variant)…
            assert html.count("Staff Engineer") == 1
            # …wearing the location picker, with the hidden variant a click away.
            assert ">2 locations ▾<" in html
            assert "New York, NY" in html
            assert "· this row" in html  # the current variant is marked, not linked
            # The raw escape shows both rows, each wearing its own picker chip.
            raw = (await c.get("/positions?collapse=0")).text
            assert raw.count("Staff Engineer") == 2
            assert raw.count(">2 locations ▾<") == 2
    finally:
        store.close()


async def test_detail_lists_role_location_variants() -> None:
    store = _role_group_store()
    try:
        pid = encode_pid("board_b", "https://acme.test/jobs/london")
        other = encode_pid("board_a", "https://acme.test/jobs/ny")
        async with _client(create_app(store)) as c:
            html = (await c.get(f"/positions/{pid}")).text
            assert "Same role in 1 other location" in html
            assert other in html  # linked to the variant's own detail page
            assert "New York, NY" in html
            # The ungrouped row renders no locations panel at all.
            solo = encode_pid("solo", "https://solo.test/jobs/1")
            assert "Same role in" not in (await c.get(f"/positions/{solo}")).text
    finally:
        store.close()


async def test_dashboard_top_matches_collapse_role_variants() -> None:
    # Both variants scored strong: without role collapse the one opening would
    # occupy two of the dashboard's top slots.
    store = _role_group_store()
    store.set_match(
        "board_a", "https://acme.test/jobs/ny", score=80, verdict="strong",
        reasons="[]",
    )
    try:
        pid_london = encode_pid("board_b", "https://acme.test/jobs/london")
        pid_ny = encode_pid("board_a", "https://acme.test/jobs/ny")
        async with _client(create_app(store)) as c:
            html = (await c.get("/")).text
            assert pid_london in html  # the better-scored variant's card
            assert pid_ny not in html  # its sibling folded into it
    finally:
        store.close()


async def test_dashboard_top_matches_one_card_per_company() -> None:
    # Two scored roles at one employer are genuinely separate leads, but the
    # shortlist shows only the company's best; another company keeps its slot,
    # and unattributed rows (blank company) never fold into each other.
    s = StateStore(":memory:")
    for url, title, company, ch in [
        ("https://acme.test/platform", "Platform Engineer", "Acme", "c1"),
        ("https://acme.test/support", "Support Engineer", "Acme", "c2"),
        ("https://beta.test/eng", "Engineer", "Beta", "c3"),
        ("https://mystery.test/1", "Ghost Role A", "", "c4"),
        ("https://mystery.test/2", "Ghost Role B", "", "c5"),
    ]:
        s.upsert_position(
            "board",
            url,
            fields={
                **FIELDS,
                "title": title,
                "company": company,
                "remote_scope": "anywhere",
            },
            content_hash=ch,
        )
    for url, score in [
        ("https://acme.test/platform", 90),
        ("https://acme.test/support", 80),
        ("https://beta.test/eng", 70),
        ("https://mystery.test/1", 65),
        ("https://mystery.test/2", 62),
    ]:
        s.set_match("board", url, score=score, verdict="strong", reasons="[]")
    try:
        async with _client(create_app(s)) as c:
            html = (await c.get("/")).text
            assert encode_pid("board", "https://acme.test/platform") in html
            assert encode_pid("board", "https://acme.test/support") not in html
            assert encode_pid("board", "https://beta.test/eng") in html
            assert encode_pid("board", "https://mystery.test/1") in html
            assert encode_pid("board", "https://mystery.test/2") in html
    finally:
        s.close()


async def test_dashboard_top_matches_only_attainable_rows() -> None:
    # The shortlist's mobility gate: a geo-fenced remote role without relocation
    # support never takes a slot, however strong the fit.
    s = StateStore(":memory:")
    for url, extra, score in [
        ("https://a.test/anywhere", {"company": "Alpha", "remote_scope": "anywhere"}, 70),
        (
            "https://b.test/relo",
            {"company": "Beta", "remote_scope": "restricted", "relocation": "offered"},
            65,
        ),
        ("https://c.test/fenced", {"company": "Gamma", "remote_scope": "restricted"}, 95),
    ]:
        s.upsert_position("board", url, fields={**FIELDS, **extra}, content_hash=url)
        s.set_match("board", url, score=score, verdict="strong", reasons="[]")
    try:
        async with _client(create_app(s)) as c:
            html = (await c.get("/")).text
            assert encode_pid("board", "https://a.test/anywhere") in html
            assert encode_pid("board", "https://b.test/relo") in html
            assert encode_pid("board", "https://c.test/fenced") not in html
    finally:
        s.close()


# -- §4.12 structured locations on the web surface -----------------------------


def _loc_store() -> StateStore:
    """Three rows spanning the projection shapes: a resolved place (display carries
    the English country name), an explicit fence, and a bare marker (no projection,
    display NULL → raw-string fallback)."""
    s = StateStore(":memory:")
    rows = [
        ("https://acme.test/sp", "São Paulo, Brasil", "hybrid", "SP Engineer"),
        ("https://acme.test/fenced", "Remote - US", "hybrid", "Fenced Engineer"),
        ("https://acme.test/bare", "Remote", "remote", "Bare Engineer"),
    ]
    for url, loc, wp, title in rows:
        s.upsert_position(
            "acme",
            url,
            fields={**FIELDS, "title": title, "location": loc, "workplace": wp},
            content_hash=url,
        )
        s.refresh_position_locations(
            "acme", url, location=loc, workplace=wp, force=True
        )
    return s


async def test_positions_rows_render_location_display_first() -> None:
    store = _loc_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/positions")).text
            assert "São Paulo, Brazil" in html  # display, not the raw 'Brasil'
            assert "Remote (United States)" in html  # the fence made visible
    finally:
        store.close()


async def test_country_and_remote_in_filters_flow_through_the_form() -> None:
    store = _loc_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/positions?country=br")).text
            assert "SP Engineer" in html
            assert "Fenced Engineer" not in html
            html = (await c.get("/positions?remote_in=us")).text
            assert "Fenced Engineer" in html
            assert "SP Engineer" not in html
            # the form inputs round-trip the active values
            assert 'name="country"' in html and 'value="us"' in html
    finally:
        store.close()


async def test_api_carries_structured_location_keys() -> None:
    store = _loc_store()
    try:
        async with _client(create_app(store)) as c:
            rows = (await c.get("/api/positions")).json()
            by_title = {r["title"]: r for r in rows}
            assert by_title["SP Engineer"]["location_display"] == "São Paulo, Brazil"
            assert by_title["Bare Engineer"]["location_display"] is None
            pid = by_title["Fenced Engineer"]["pid"]
            detail = (await c.get(f"/api/positions/{pid}")).json()
            assert detail["location_display"] == "Remote (United States)"
            assert detail["locations"] == [
                {
                    "kind": "remote_fence",
                    "city": None,
                    "subdivision": None,
                    "country": "US",
                    "macro_region": None,
                    "display": "Remote (United States)",
                    "source": "rules",
                    "confidence": "high",
                }
            ]
    finally:
        store.close()


async def test_detail_source_board_links_to_the_sites_positions() -> None:
    # The provenance "Source board" is a filter jump: click it to browse every
    # position the crawler currently tracks.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get(f"/positions/{_ACME_PID}")).text
            assert 'href="/positions?site=acme"' in html
    finally:
        store.close()


async def test_relocation_non_us_checkbox_filters_and_round_trips() -> None:
    # One checkbox = relocation offered AND no resolved US office/fence: visa
    # entry to the US is the hard one, so US-destination relocations drop while
    # unknown-geography relocation rows stay.
    s = StateStore(":memory:")
    try:
        rows = [
            ("https://a.test/de", "DE Engineer", "Berlin, Germany", "offered"),
            ("https://a.test/us", "US Engineer", "San Francisco, CA", "offered"),
            ("https://a.test/fence", "Fence Engineer", "Remote - US", "offered"),
            ("https://a.test/norelo", "NoRelo Engineer", "Berlin, Germany", "unspecified"),
        ]
        for url, title, loc, relo in rows:
            s.upsert_position(
                "acme",
                url,
                fields={**FIELDS, "title": title, "location": loc,
                        "workplace": "hybrid", "relocation": relo},
                content_hash=url,
            )
            s.refresh_position_locations(
                "acme", url, location=loc, workplace="hybrid", force=True
            )
        async with _client(create_app(s)) as c:
            html = (await c.get("/positions?relocation_no_us=1")).text
            assert "DE Engineer" in html
            assert "US Engineer" not in html
            assert "Fence Engineer" not in html
            assert "NoRelo Engineer" not in html
            assert 'name="relocation_no_us"' in html and "checked" in html
    finally:
        s.close()


async def test_keyboard_help_overlay_lists_both_modes() -> None:
    # '?' toggles the shortcut card (base shell, every page): it must document the
    # list bindings AND the panel-mode bindings that act on the shown position.
    store = _seeded_store()
    try:
        async with _client(create_app(store)) as c:
            html = (await c.get("/positions")).text
            assert 'id="kbd-help"' in html
            assert "Keyboard shortcuts" in html
            assert "With the position panel open" in html
            for hint in ("panel follows", "Act on the shown position", "Jump to notes"):
                assert hint in html, hint
    finally:
        store.close()


async def test_salary_provenance_reaches_api_and_detail() -> None:
    # The operator can see WHERE a figure came from: source data, the printed
    # text, or the §4.7 LLM backfill (badged) — and the as-printed string rides
    # along for rows whose parse was refused.
    s = StateStore(":memory:")
    try:
        s.upsert_position(
            "acme",
            "https://acme.test/paid",
            fields={
                **FIELDS,
                "title": "Paid Engineer",
                "salary_min": 150000,
                "salary_max": 200000,
                "salary_currency": "USD",
                "salary_period": "year",
                "salary_raw": "$150K – $200K",
                "salary_source": "text",
            },
            content_hash="h1",
        )
        s.upsert_position(
            "acme",
            "https://acme.test/mined",
            fields={**FIELDS, "title": "Mined Engineer"},
            content_hash="h2",
        )
        s.set_salary(
            "acme",
            "https://acme.test/mined",
            salary_min=90000.0,
            salary_max=90000.0,
            salary_currency="USD",
            salary_period="year",
        )
        async with _client(create_app(s)) as c:
            rows = (await c.get("/api/positions")).json()
            by_title = {r["title"]: r for r in rows}
            assert by_title["Paid Engineer"]["salary_source"] == "text"
            assert by_title["Paid Engineer"]["salary_raw"] == "$150K – $200K"
            assert by_title["Mined Engineer"]["salary_source"] == "llm"

            pid = by_title["Mined Engineer"]["pid"]
            html = (await c.get(f"/positions/{pid}")).text
            assert "LLM-extracted" in html  # the mined figure is badged
            pid = by_title["Paid Engineer"]["pid"]
            html = (await c.get(f"/positions/{pid}")).text
            assert "$150K – $200K" in html  # as printed
    finally:
        s.close()


async def test_detail_page_shows_locations_panel_and_display_provenance() -> None:
    store = _loc_store()
    try:
        async with _client(create_app(store)) as c:
            pid = encode_pid("acme", "https://acme.test/fenced")
            html = (await c.get(f"/positions/{pid}")).text
            # header + Locations panel carry the resolved fence; provenance keeps
            # raw and normalized AND now names the display form.
            assert html.count("Remote (United States)") >= 2
            assert "Location (display)" in html
            assert "Remote - US" in html  # raw provenance row unchanged
    finally:
        store.close()
