"""Tests for §17 cross-board duplicate clustering — the read-derived identity overlay.

Two layers under test. The pure key-derivation functions (``ats_keys``,
``normalize_apply_url``, ``identity_keys``) are pinned against the measured hazard
corpus: every guard here exists because the live DB demonstrated the false merge it
prevents (robinhood same-title reqs, the grafana separator, the anthropic dual-req
hash, the '630' cross-family collision, the jotform shared signup form…). The sweep
(``resweep``) and the store surfaces it feeds (verdict donor, notify gate, collapse
queries) run against a real in-memory StateStore — no fakes, no LLM, no network:
§17 is a pure deterministic function of stored fields and must stay one.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from jobcrawler.runtime.dedup import (
    IDENTITY_APPLY_TRACKING_PARAMS,
    TIER_APPLY,
    TIER_ATS,
    TIER_CONTENT,
    TIER_FAMILY,
    TIER_FUZZY,
    TIER_LINKJOB,
    TIER_URL,
    ats_keys,
    identity_keys,
    normalize_apply_url,
    resweep,
)
from jobcrawler.state import StateStore

DEFAULT_TIERS = frozenset({0, 1, 2, 3, 6})


@pytest.fixture
def store() -> Iterator[StateStore]:
    s = StateStore(":memory:")
    yield s
    s.close()


def _put(
    store: StateStore,
    site: str,
    url: str,
    *,
    title: str = "Senior Backend Engineer",
    company: str = "Acme",
    location: str = "Remote",
    description: str = "We build distributed payment systems in Rust." * 3,
    content_hash: str | None = None,
    **extra: object,
) -> None:
    fields: dict[str, object] = {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
    }
    fields.update(extra)
    store.upsert_position(
        site, url, fields=fields, content_hash=content_hash or f"h:{site}:{url}"
    )


def _sweep(store: StateStore, *, tiers=DEFAULT_TIERS, direct_sites=(), **kw):
    return resweep(store, direct_sites=direct_sites, tiers=tiers, **kw)


def _clusters(store: StateStore) -> dict[str, set[tuple[str, str]]]:
    rows = store._conn.execute(
        "SELECT site, url, cluster_id FROM position_clusters"
    ).fetchall()
    out: dict[str, set[tuple[str, str]]] = {}
    for r in rows:
        out.setdefault(r["cluster_id"], set()).add((r["site"], r["url"]))
    return out


def _canonical_of(store: StateStore, site: str, url: str) -> tuple[str, str] | None:
    members = store.cluster_members(site, url)
    return (members[0][0].site, members[0][0].url) if members else None


# --- T1/T6 key extraction (ats_keys) -----------------------------------------


def test_greenhouse_forms_all_share_one_key() -> None:
    board = ats_keys("https://boards.greenhouse.io/mongodb/jobs/5970004")
    api = ats_keys("https://boards-api.greenhouse.io/v1/boards/mongodb/jobs/5970004")
    embed = ats_keys(
        "https://boards.greenhouse.io/embed/job_app?for=mongodb&token=5970004"
    )
    jid = ats_keys("https://www.mongodb.com/careers/job?gh_jid=5970004")
    assert {k.key for k in board + api + embed + jid} == {"ats:greenhouse:5970004"}
    assert board[0].org == "mongodb" and embed[0].org == "mongodb"
    assert jid[0].org is None  # bare gh_jid carries no tenant evidence


def test_greenhouse_format_guard_rejects_short_and_slug_ids() -> None:
    # Short numeric (< 5 digits) and slug captures are junk, never global keys.
    assert ats_keys("https://boards.greenhouse.io/acme/jobs/4001") == []
    assert ats_keys("https://careers.acme.test/job?gh_jid=630") == []
    assert ats_keys("https://boards.greenhouse.io/acme/jobs/staff-engineer") == []


def test_ashby_lever_and_query_param_forms() -> None:
    uuid = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
    ash = ats_keys(f"https://jobs.ashbyhq.com/openai/{uuid}/application")
    jid = ats_keys(f"https://careers.example.test/openai?ashby_jid={uuid}")
    lev = ats_keys(f"https://jobs.lever.co/kraken/{uuid}/apply")
    assert ash[0].key == jid[0].key == f"ats:ashby:{uuid}"
    assert ash[0].org == "openai" and jid[0].org is None
    assert lev[0].key == f"ats:lever:{uuid}" and lev[0].org == "kraken"
    # Non-UUID second segment is a listing page, not a posting.
    assert ats_keys("https://jobs.ashbyhq.com/openai/engineering") == []


def test_tenant_scoped_vendors_and_workday_req_guard() -> None:
    rec = ats_keys("https://acme.recruitee.com/o/senior-rust-engineer")
    assert rec[0].key == "ats:recruitee:acme:senior-rust-engineer"
    wd = ats_keys(
        "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Remote/Engineer_JR-12345"
    )
    assert wd[0].key == "ats:workday:acme:jr-12345"
    # A title-only last segment (no digit in the req suffix) never keys.
    assert (
        ats_keys("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Staff-Engineer")
        == []
    )
    wk = ats_keys("https://apply.workable.com/nethermind/j/A1B2C3D4E5/")
    assert wk[0].key == "ats:workable:a1b2c3d4e5"
    sr = ats_keys("https://jobs.smartrecruiters.com/Acme/743999912345678-engineer")
    assert sr[0].key == "ats:smartrecruiters:743999912345678"


def test_t6_linkedin_indeed_posting_ids_only() -> None:
    li = ats_keys("https://www.linkedin.com/jobs/view/4242424242/")
    cur = ats_keys("https://www.linkedin.com/jobs/search/?currentJobId=4242424242")
    ind = ats_keys("https://www.indeed.com/viewjob?jk=abcdef123456")
    assert li[0] == cur[0] and li[0].key == "li:4242424242"
    assert li[0].tier == TIER_LINKJOB
    assert ind[0].key == "indeed:abcdef123456"
    # Search/listing URLs without a per-posting id never key.
    assert ats_keys("https://www.linkedin.com/jobs/search/?keywords=rust") == []
    assert ats_keys("https://www.indeed.com/jobs?q=rust&l=Remote") == []


def test_junk_urls_yield_no_keys() -> None:
    for junk in ("", "mailto:jobs@acme.test", "not a url", "ftp://x.test/a",
                 "https://boards.greenhouse.io"):
        assert ats_keys(junk) == []


# --- T2 apply-url normalization ----------------------------------------------


def test_normalize_apply_url_strips_tracking_but_keeps_identity_params() -> None:
    norm = normalize_apply_url(
        "https://boards.greenhouse.io/acme/jobs/400123"
        "?gh_src=abc123&utm_source=board&ref=aggr&t=99",
        own_url="https://aggregator.test/job/1",
    )
    assert norm == "https://boards.greenhouse.io/acme/jobs/400123"
    # Identity-BEARING params must survive — dice's applyData, greenhouse's gh_jid,
    # indeed's jk… stripping them would fuse every job behind one apply endpoint.
    for param in ("gh_jid=400123", "ashby_jid=x", "for=acme", "token=1",
                  "jk=abc", "pid=7", "applyData=eyJhbGci"):
        name = param.split("=")[0]
        assert name not in IDENTITY_APPLY_TRACKING_PARAMS
        norm = normalize_apply_url(
            f"https://careers.dice.test/apply?{param}&utm_medium=feed",
            own_url="https://aggregator.test/job/2",
        )
        assert norm is not None and param in norm and "utm_medium" not in norm


def test_normalize_apply_url_emission_guards() -> None:
    own = "https://board.test/jobs/1"
    # Same-page anchor: not cross-board evidence.
    assert normalize_apply_url("https://board.test/jobs/1#apply", own_url=own) is None
    # Talent-marketplace pool host: every listing shares one generic signup.
    assert (
        normalize_apply_url(
            "https://app.marketplace.test/signup",
            own_url=own,
            marketplace_hosts=("marketplace.test",),
        )
        is None
    )
    # Relative / junk forms.
    assert normalize_apply_url("/apply", own_url=own) is None
    assert normalize_apply_url("mailto:jobs@acme.test", own_url=own) is None
    assert normalize_apply_url(None, own_url=own) is None


# --- identity_keys assembly ---------------------------------------------------


def _keys(site="board_a", url="https://board-a.test/jobs/1", **kw):
    defaults: dict[str, Any] = dict(
        apply_url=None,
        external_id=None,
        company="Acme",
        company_canonical=None,
        title="Senior Backend Engineer",
        content_hash="hash01",
    )
    defaults.update(kw)
    return identity_keys(site, url, **defaults)


def test_identity_keys_t0_canonicalizes_and_dedupes() -> None:
    keys = _keys(url="https://board-a.test/jobs/1?utm_source=feed")
    t0 = [k for k in keys if k.tier == TIER_URL]
    assert t0 == [t0[0]] and t0[0].key == "url:https://board-a.test/jobs/1"


def test_identity_keys_t2_namespace_split() -> None:
    # Per-job ATS host → edge-safe 'applyats:'; anything else → guarded 'apply:'.
    ats = _keys(apply_url="https://boards.greenhouse.io/acme/jobs/4001")
    generic = _keys(apply_url="https://forms.jotform.test/apply/99")
    assert any(k.key.startswith("applyats:") for k in ats if k.tier == TIER_APPLY)
    assert any(k.key.startswith("apply:") for k in generic if k.tier == TIER_APPLY)


def test_identity_keys_t1_extracted_from_apply_url_too() -> None:
    # The sequoia↔ashby stub-fusion shape: a Getro aggregator row whose apply link
    # is the tenant's Ashby posting must join the board row via T1.
    uuid = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
    keys = _keys(
        site="sequoia_portfolio_jobs",
        url="https://jobs.sequoiacap.com/companies/x/jobs/123",
        apply_url=f"https://jobs.ashbyhq.com/xai/{uuid}",
    )
    assert f"ats:ashby:{uuid}" in {k.key for k in keys if k.tier == TIER_ATS}


def test_identity_keys_t3_family_scoped_never_global() -> None:
    # The measured '630' hazard: the same small integer external_id on unrelated
    # boards must never key globally — only within a declared slug family.
    wn = _keys(site="workingnomads_rust", external_id="630")
    arc = _keys(site="arc_rust", external_id="630")
    other = _keys(site="board_a", external_id="630")
    assert {k.key for k in wn if k.tier == TIER_FAMILY} == {"fam:workingnomads:630"}
    assert {k.key for k in arc if k.tier == TIER_FAMILY} == {"fam:arc:630"}
    assert [k for k in other if k.tier == TIER_FAMILY] == []


def test_identity_keys_company_tier_guards() -> None:
    # HN's company column holds handles/locations — no company-keyed tiers.
    hn = _keys(site="hn_whos_hiring", company="SF Bay Area")
    assert all(k.tier not in (TIER_CONTENT, TIER_FUZZY) for k in hn)
    # Degenerate (<3 chars) company key — the measured 'op' collision.
    tiny = _keys(company="op", company_canonical=None)
    assert all(k.tier not in (TIER_CONTENT, TIER_FUZZY) for k in tiny)
    # Normal rows carry both shadow keys (edges are the sweep's decision, not here).
    normal = _keys()
    tiers = {k.tier for k in normal}
    assert TIER_CONTENT in tiers and TIER_FUZZY in tiers


# --- resweep: joins the corpus says are safe -----------------------------------


def test_sweep_t1_joins_across_boards_and_survives_title_drift(store) -> None:
    # mongodb shape (board row + careers page via gh_jid) + figma shape (the
    # aggregator retitled the posting — T1 identity must still join).
    _put(store, "greenhouse_mongodb",
         "https://boards.greenhouse.io/mongodb/jobs/5970004",
         title="Senior Backend Engineer")
    _put(store, "mongodb_careers",
         "https://www.mongodb.com/careers/job?gh_jid=5970004",
         title="Sr. Backend Engineer (Distributed Systems)")
    _put(store, "other_board", "https://other.test/jobs/999")  # bystander

    report = _sweep(store)

    assert report["clusters"] == 1 and report["clustered_rows"] == 2
    assert report["edges_by_tier"]["1"] == 1
    clusters = _clusters(store)
    (members,) = clusters.values()
    assert members == {
        ("greenhouse_mongodb", "https://boards.greenhouse.io/mongodb/jobs/5970004"),
        ("mongodb_careers", "https://www.mongodb.com/careers/job?gh_jid=5970004"),
    }


def test_sweep_t1_org_conflict_vetoes_and_autoblocklists(store) -> None:
    # One (vendor, id) under two tenants: recycled id, not identity. The key must
    # form no edge AND land on the blocklist so live gates can't join through it.
    _put(store, "board_a", "https://boards.greenhouse.io/acme/jobs/400123")
    _put(store, "board_b", "https://boards.greenhouse.io/globex/jobs/400123",
         company="Globex")

    report = _sweep(store)

    assert report["clusters"] == 0
    assert report["guards"]["t1_org_conflict"] == 1
    assert "ats:greenhouse:400123" in store.blocked_key_set()
    reasons = {b["key"]: b["reason"] for b in store.blocked_keys()}
    assert "org conflict" in (reasons["ats:greenhouse:400123"] or "")


def test_sweep_higher_tier_separator_grafana_shape(store) -> None:
    # Two rows carrying DIFFERENT T1 identities share weaker evidence (same company
    # + identical content hash — boilerplate text). The separator must win: distinct
    # reqs can never be fused by a weaker tier.
    _put(store, "board_a", "https://boards.greenhouse.io/grafana/jobs/400111",
         company="Grafana Labs", content_hash="samehash")
    _put(store, "board_b", "https://boards.greenhouse.io/grafana/jobs/400222",
         company="Grafana Labs", content_hash="samehash")

    report = _sweep(store, tiers=frozenset({0, 1, 4}))

    assert report["clusters"] == 0
    assert report["guards"]["separator_splits"] >= 1


def test_sweep_t4_ambiguity_veto_anthropic_dual_req_shape(store) -> None:
    # One site holds the same content hash twice (same text, two live reqs — the
    # anthropic/ramp shape). The hash is not identity for ANY pair of this key,
    # including the cross-site one.
    _put(store, "board_a", "https://a.test/jobs/1", content_hash="dup",
         company="Anthropic")
    _put(store, "board_a", "https://a.test/jobs/2", content_hash="dup",
         company="Anthropic")
    _put(store, "board_b", "https://b.test/jobs/9", content_hash="dup",
         company="Anthropic")

    report = _sweep(store, tiers=frozenset({0, 4}))

    assert report["clusters"] == 0
    assert report["guards"]["t4_hash_ambiguous"] == 1


def test_sweep_robinhood_same_title_reqs_stay_singletons(store) -> None:
    # The robinhood hazard: nine open same-title rows on ONE board are nine reqs.
    # Even with the fuzzy tier force-enabled, same-site pairs never join and the
    # open-count veto kills the key — no tier set may fuse them.
    for i in range(9):
        _put(store, "robinhood", f"https://careers.robinhood.test/jobs/{i}",
             title="Senior Software Engineer", company="Robinhood",
             content_hash=f"rh{i}")

    report = _sweep(store, tiers=frozenset({0, 1, 2, 3, 5, 6}))

    assert report["clusters"] == 0


def test_sweep_t2_applyats_equality_is_identity(store) -> None:
    # Two boards pointing at one per-job ATS apply link. The link's id is too short
    # for the T1 format guard (4 digits), so ONLY the applyats: namespace joins them
    # — exactly the tier this fixture pins.
    apply = "https://boards.greenhouse.io/acme/jobs/4001"
    _put(store, "board_a", "https://a.test/jobs/1", apply_url=apply)
    _put(store, "board_b", "https://b.test/jobs/2", apply_url=apply)

    report = _sweep(store)

    assert report["clusters"] == 1
    assert report["edges_by_tier"]["2"] == 1


def test_sweep_generic_apply_needs_corroboration(store) -> None:
    # remoteva/jotform shape: a shared generic form URL is NOT identity on its own.
    # Different companies → reject.
    form = "https://forms.jotform.test/apply/99"
    _put(store, "board_a", "https://a.test/jobs/1", apply_url=form, company="Acme")
    _put(store, "board_b", "https://b.test/jobs/2", apply_url=form, company="Globex")
    report = _sweep(store, tiers=frozenset({0, 2}))
    assert report["clusters"] == 0
    assert report["guards"]["t2_generic_rejects"] >= 1

    # Same company + same title + single candidate per site → corroborated join.
    s2 = StateStore(":memory:")
    try:
        _put(s2, "board_a", "https://a.test/jobs/1", apply_url=form, company="Acme")
        _put(s2, "board_b", "https://b.test/jobs/2", apply_url=form, company="Acme")
        report2 = _sweep(s2, tiers=frozenset({0, 2}))
        assert report2["clusters"] == 1
    finally:
        s2.close()


def test_sweep_generic_apply_rejected_when_a_site_reuses_it(store) -> None:
    # A board holding the same generic apply URL on TWO rows is a shared signup
    # form — every pair through that key is rejected, even the cross-site one
    # with matching company/title.
    form = "https://forms.jotform.test/apply/shared"
    _put(store, "board_a", "https://a.test/jobs/1", apply_url=form)
    _put(store, "board_a", "https://a.test/jobs/2", apply_url=form)
    _put(store, "board_b", "https://b.test/jobs/9", apply_url=form)

    report = _sweep(store, tiers=frozenset({0, 2}))

    assert report["clusters"] == 0
    assert report["guards"]["t2_generic_rejects"] >= 1


def test_sweep_t3_joins_inside_family_never_across(store) -> None:
    _put(store, "workingnomads_golang", "https://wn.test/golang/630",
         external_id="630")
    _put(store, "workingnomads_rust", "https://wn.test/rust/630",
         external_id="630")
    _put(store, "arc_rust", "https://arc.test/rust/630", external_id="630")

    report = _sweep(store)

    clusters = _clusters(store)
    assert report["clusters"] == 1
    (members,) = clusters.values()
    assert {s for s, _ in members} == {"workingnomads_golang", "workingnomads_rust"}


def test_sweep_blocklisted_key_forms_no_edges(store) -> None:
    _put(store, "board_a", "https://boards.greenhouse.io/acme/jobs/400123")
    _put(store, "board_b", "https://acme.test/careers?gh_jid=400123")
    store.block_identity_key("ats:greenhouse:400123", reason="operator: bad merge")

    report = _sweep(store)

    assert report["clusters"] == 0
    assert report["guards"]["blocklist_splits"] >= 1


def test_sweep_degenerate_key_is_skipped_not_clustered(store) -> None:
    # >100 rows behind one key = an extraction bug, not one job with 101 copies.
    for i in range(101):
        _put(store, f"site_{i}", f"https://s{i}.test/job",
             company=f"Co {i}", content_hash=f"h{i}",
             apply_url="https://careers.hub.test/apply?gh_jid=400777")

    report = _sweep(store)

    assert report["clusters"] == 0
    # The shared apply URL mints two degenerate keys (its gh_jid T1 id and the
    # generic T2 link) — BOTH must be skipped, whatever their tier.
    assert report["guards"]["degenerate_keys"] == 2


# --- resweep: election, determinism, independence ------------------------------


def _pair(store, **b_extra) -> None:
    """Two boards, one greenhouse req — the smallest real cluster."""
    _put(store, "board_a", "https://boards.greenhouse.io/acme/jobs/400123",
         description="short")
    _put(store, "board_b", "https://acme.test/careers?gh_jid=400123",
         description="a much longer and more informative description " * 5,
         **b_extra)


def test_election_prefers_longest_description_then_upgrades_to_open(store) -> None:
    _pair(store)
    _sweep(store)
    assert _canonical_of(store, "board_a",
                         "https://boards.greenhouse.io/acme/jobs/400123") == (
        "board_b", "https://acme.test/careers?gh_jid=400123")

    # The canonical's board closes it → the open sibling takes over next sweep.
    store.mark_closed("board_b", "https://acme.test/careers?gh_jid=400123",
                      reason="missing")
    _sweep(store)
    assert _canonical_of(store, "board_b",
                         "https://acme.test/careers?gh_jid=400123") == (
        "board_a", "https://boards.greenhouse.io/acme/jobs/400123")


def test_election_prefers_direct_site_and_applied_overrides_all(store) -> None:
    _pair(store)
    # Direct company board beats the aggregator even with a shorter description.
    _sweep(store, direct_sites={"board_a"})
    assert _canonical_of(store, "board_b",
                         "https://acme.test/careers?gh_jid=400123") == (
        "board_a", "https://boards.greenhouse.io/acme/jobs/400123")

    # …but the copy the operator APPLIED to pins the cluster forever.
    store.set_track_status("board_b", "https://acme.test/careers?gh_jid=400123",
                           "applied", stamp_applied=True)
    _sweep(store, direct_sites={"board_a"})
    assert _canonical_of(store, "board_a",
                         "https://boards.greenhouse.io/acme/jobs/400123") == (
        "board_b", "https://acme.test/careers?gh_jid=400123")


def test_sweep_is_deterministic_and_idempotent(store) -> None:
    _pair(store)
    _put(store, "solo", "https://solo.test/jobs/1", company="Solo Co")

    r1 = _sweep(store)
    overlay1 = store._conn.execute(
        "SELECT site, url, cluster_id, is_canonical, tier FROM position_clusters "
        "ORDER BY site, url"
    ).fetchall()
    r2 = _sweep(store)
    overlay2 = store._conn.execute(
        "SELECT site, url, cluster_id, is_canonical, tier FROM position_clusters "
        "ORDER BY site, url"
    ).fetchall()

    assert [tuple(r) for r in overlay1] == [tuple(r) for r in overlay2]
    for volatile in ("swept_at",):
        r1.pop(volatile), r2.pop(volatile)
    assert r1 == r2
    # The report is persisted for the CLI/report path.
    assert json.loads(store.get_meta("dedup_last_report"))["clusters"] == 1


def test_sweep_never_touches_lifecycle_or_verdicts(store) -> None:
    # §17 boundary: clustering interprets rows, it never mutates them. Guarded
    # closure (per-board lifecycle) and match verdicts are untouchable.
    _pair(store)
    store.set_match("board_a", "https://boards.greenhouse.io/acme/jobs/400123",
                    score=88, verdict="strong", reasons="[]", origin="llm")
    store.mark_closed("board_b", "https://acme.test/careers?gh_jid=400123",
                      reason="missing")
    before = store._conn.execute(
        "SELECT site, url, status, closed_at, dismissed, match_score, match_verdict, "
        "match_origin, track_status FROM positions ORDER BY site, url"
    ).fetchall()

    _sweep(store)

    after = store._conn.execute(
        "SELECT site, url, status, closed_at, dismissed, match_score, match_verdict, "
        "match_origin, track_status FROM positions ORDER BY site, url"
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


def test_sweep_report_flags_conflicts_and_dismissal_leaks(store) -> None:
    _pair(store)
    store.set_match("board_a", "https://boards.greenhouse.io/acme/jobs/400123",
                    score=88, verdict="strong", reasons="[]", origin="llm")
    store.set_match("board_b", "https://acme.test/careers?gh_jid=400123",
                    score=30, verdict="weak", reasons="[]", origin="llm")
    store.set_dismissed("board_a",
                        "https://boards.greenhouse.io/acme/jobs/400123", True)

    report = _sweep(store)

    assert report["multi_matched_clusters"] == 1
    assert report["conflicting_verdict_clusters"] == 1
    # board_b is open, unhandled, in a cluster with a dismissed member — the row
    # the read-time suppression exists to hide.
    assert report["dismissal_leak_rows"] == 1
    assert report["top_clusters"][0]["size"] == 2


# --- store surfaces: live donor, notify gate, reversal --------------------------


def test_donor_via_live_keys_before_any_sweep(store) -> None:
    # The ingest hook wrote both rows' keys; no sweep has run (empty overlay).
    # A sibling scored seconds earlier must already be visible.
    _put(store, "board_a", "https://a.test/jobs/1",
         description="full text " * 50)
    _put(store, "board_b", "https://b.test/jobs/2")
    store.set_match("board_a", "https://a.test/jobs/1",
                    score=88, verdict="strong", reasons='["fit"]', origin="llm")
    store.replace_identity_keys("board_a", "https://a.test/jobs/1",
                                [(TIER_ATS, "ats:greenhouse:400123")])
    store.replace_identity_keys("board_b", "https://b.test/jobs/2",
                                [(TIER_ATS, "ats:greenhouse:400123")])

    donor = store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=10, tiers=DEFAULT_TIERS
    )
    assert donor is not None and donor.site == "board_a"
    assert donor.match_score == 88

    # The stub guard: a donor thinner than the candidate's digest never donates.
    assert store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=10_000, tiers=DEFAULT_TIERS
    ) is None

    # A parked donor (profile edit re-queued it) is not a settled verdict.
    store.set_match_pending("board_a", "https://a.test/jobs/1")
    assert store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=10, tiers=DEFAULT_TIERS
    ) is None


def test_donor_live_join_respects_tier_gate_and_blocklist(store) -> None:
    _put(store, "board_a", "https://a.test/jobs/1", description="full text " * 50)
    _put(store, "board_b", "https://b.test/jobs/2")
    store.set_match("board_a", "https://a.test/jobs/1",
                    score=70, verdict="good", reasons="[]", origin="llm")
    # A generic 'apply:' T2 key is NEVER live-safe (needs sweep corroboration).
    for site, url in (("board_a", "https://a.test/jobs/1"),
                      ("board_b", "https://b.test/jobs/2")):
        store.replace_identity_keys(
            site, url, [(TIER_APPLY, "apply:https://forms.test/1")]
        )
    assert store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=0, tiers=DEFAULT_TIERS
    ) is None

    # An applyats: T2 key IS live-safe…
    for site, url in (("board_a", "https://a.test/jobs/1"),
                      ("board_b", "https://b.test/jobs/2")):
        store.replace_identity_keys(
            site, url, [(TIER_APPLY, "applyats:https://boards.greenhouse.io/a/jobs/4001")]
        )
    donor = store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=0, tiers=DEFAULT_TIERS
    )
    assert donor is not None
    # …unless the operator blocked the key.
    store.block_identity_key("applyats:https://boards.greenhouse.io/a/jobs/4001")
    assert store.cluster_verdict_donor(
        "board_b", "https://b.test/jobs/2", min_desc_len=0, tiers=DEFAULT_TIERS
    ) is None


def test_donor_via_materialized_cluster_even_with_no_live_tiers(store) -> None:
    # The sweep already guarded the materialized edges, so the overlay half of the
    # donor lookup is not tier-gated — an empty live set still finds the sibling.
    _pair(store)
    store.set_match("board_b", "https://acme.test/careers?gh_jid=400123",
                    score=91, verdict="strong", reasons="[]", origin="llm")
    _sweep(store)

    donor = store.cluster_verdict_donor(
        "board_a", "https://boards.greenhouse.io/acme/jobs/400123",
        min_desc_len=5, tiers=frozenset(),
    )
    assert donor is not None and donor.site == "board_b"


def test_requeue_inherited_reverses_only_cluster_verdicts(store) -> None:
    for i, origin in enumerate(("llm", "cache", "cluster:board_x\nhttps://x.test/1")):
        _put(store, "board", f"https://board.test/jobs/{i}")
        store.set_match("board", f"https://board.test/jobs/{i}",
                        score=80, verdict="strong", reasons="[]", origin=origin)

    assert store.requeue_inherited() == 1

    rows = {
        r["url"]: r
        for r in store._conn.execute(
            "SELECT url, match_pending, match_origin FROM positions"
        ).fetchall()
    }
    assert rows["https://board.test/jobs/2"]["match_pending"] == 1
    assert rows["https://board.test/jobs/2"]["match_origin"] is None
    assert rows["https://board.test/jobs/0"]["match_pending"] == 0
    assert rows["https://board.test/jobs/0"]["match_origin"] == "llm"
    assert store.requeue_inherited() == 0  # idempotent: nothing left to reverse


def test_notify_gate_one_ping_per_cluster(store) -> None:
    _pair(store)
    _put(store, "board_c", "https://c.test/jobs/9", company="Bystander Co")
    _sweep(store)
    a = ("board_a", "https://boards.greenhouse.io/acme/jobs/400123")
    b = ("board_b", "https://acme.test/careers?gh_jid=400123")

    # First copy pings; the recorded fact silences the sibling — including inside
    # one drain batch (the gate is re-checked per emit).
    assert store.notify_gate(*a, tiers=DEFAULT_TIERS) is True
    store.record_notified("match", *a)
    assert store.notify_gate(*a, tiers=DEFAULT_TIERS) is False
    assert store.notify_gate(*b, tiers=DEFAULT_TIERS) is False
    # An unrelated row is untouched.
    assert store.notify_gate("board_c", "https://c.test/jobs/9",
                             tiers=DEFAULT_TIERS) is True


def test_notify_gate_dismissed_or_applied_sibling_suppresses(store) -> None:
    _pair(store)
    _sweep(store)
    store.set_dismissed("board_a",
                        "https://boards.greenhouse.io/acme/jobs/400123", True)
    # No ledger fact anywhere — the handled SIBLING alone must silence the ping
    # (a job you dismissed under one board never pings from another).
    assert store.notify_gate("board_b", "https://acme.test/careers?gh_jid=400123",
                             tiers=DEFAULT_TIERS) is False


def test_notify_ledger_seed_counts_existing_strong_matches(store, monkeypatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_MIN_SCORE", "70")
    _put(store, "board_a", "https://a.test/jobs/1")
    _put(store, "board_b", "https://b.test/jobs/2")
    store.set_match("board_a", "https://a.test/jobs/1",
                    score=90, verdict="strong", reasons="[]")
    store.set_match("board_b", "https://b.test/jobs/2",
                    score=40, verdict="weak", reasons="[]")

    store.seed_notify_ledger()

    # The pre-feature strong match now counts as notified; the weak row doesn't.
    assert store.notify_gate("board_a", "https://a.test/jobs/1",
                             tiers=DEFAULT_TIERS) is False
    assert store.notify_gate("board_b", "https://b.test/jobs/2",
                             tiers=DEFAULT_TIERS) is True
    assert (store.get_meta("notify_ledger_seeded") or "").startswith("threshold=")


# --- store surfaces: collapse reads --------------------------------------------


def _urls(rows) -> set[str]:
    return {p.url for p in rows}


def test_collapse_hides_non_canonical_and_raw_shows_all(store) -> None:
    _pair(store)
    _put(store, "solo", "https://solo.test/jobs/1", company="Solo Co")
    _sweep(store)

    raw = store.search_positions(limit=100)
    collapsed = store.search_positions(collapse_duplicates=True, limit=100)

    assert _urls(raw) == {
        "https://boards.greenhouse.io/acme/jobs/400123",
        "https://acme.test/careers?gh_jid=400123",
        "https://solo.test/jobs/1",
    }
    # One row per real-world job: the elected canonical + the standalone row.
    assert _urls(collapsed) == {
        "https://acme.test/careers?gh_jid=400123",  # longest description won
        "https://solo.test/jobs/1",
    }


def test_flags_off_is_byte_identical_before_and_after_sweep(store) -> None:
    _pair(store)
    before = [(p.site, p.url) for p in store.search_positions(limit=100)]
    _sweep(store)
    after = [(p.site, p.url) for p in store.search_positions(limit=100)]
    assert before == after


def test_anti_resurrection_dismissing_one_copy_retires_the_cluster(store) -> None:
    _pair(store)
    _sweep(store)
    store.set_dismissed("board_b", "https://acme.test/careers?gh_jid=400123", True)

    # dismissed=False alone (the pre-§17 default browse) still shows board_a — the
    # measured resurrection. exclude_cluster_handled retires the whole cluster.
    legacy = store.search_positions(dismissed=False, limit=100)
    assert "https://boards.greenhouse.io/acme/jobs/400123" in _urls(legacy)
    suppressed = store.search_positions(
        dismissed=False, collapse_duplicates=True, exclude_cluster_handled=True,
        limit=100,
    )
    assert _urls(suppressed) == set()

    # …and it survives the dismissed copy's board closing the row (the ledger of
    # record is the positions row, which closure never deletes).
    store.mark_closed("board_b", "https://acme.test/careers?gh_jid=400123",
                      reason="missing")
    _sweep(store)
    still = store.search_positions(
        dismissed=False, collapse_duplicates=True, exclude_cluster_handled=True,
        limit=100,
    )
    assert _urls(still) == set()


def test_user_data_queries_bypass_collapse(store) -> None:
    _pair(store)
    _sweep(store)
    b = ("board_b", "https://acme.test/careers?gh_jid=400123")
    store.set_dismissed(*b, True)

    # Dismissed view: the operator's own action must stay reachable even though
    # the row's cluster is "handled" and the row may be non-canonical.
    got = store.search_positions(
        dismissed=True, collapse_duplicates=True, exclude_cluster_handled=True,
        limit=100,
    )
    assert _urls(got) == {b[1]}

    # Applications view: applied rows are pinned forever.
    store.set_dismissed(*b, False)
    store.set_track_status(*b, "applied", stamp_applied=True)
    got = store.search_positions(
        applied=True, collapse_duplicates=True, exclude_cluster_handled=True,
        limit=100,
    )
    assert _urls(got) == {b[1]}

    # An operator-set stage bypasses; the schema-default 'new' does NOT (it is
    # exactly the untriaged view collapsing exists for).
    got = store.search_positions(
        track_status="applied", collapse_duplicates=True,
        exclude_cluster_handled=True, limit=100,
    )
    assert _urls(got) == {b[1]}
    got = store.search_positions(
        track_status="new", collapse_duplicates=True, limit=100,
    )
    assert b[1] not in _urls(got)  # non-canonical members stay collapsed


def test_cluster_members_sizes_and_overlay_stats(store) -> None:
    _pair(store)
    _put(store, "solo", "https://solo.test/jobs/1", company="Solo Co")
    _sweep(store)
    a = ("board_a", "https://boards.greenhouse.io/acme/jobs/400123")
    b = ("board_b", "https://acme.test/careers?gh_jid=400123")

    members = store.cluster_members(*a)
    assert [(m.site for m, _ in members)] and len(members) == 2
    assert members[0][1] is True and (members[0][0].site, members[0][0].url) == b
    assert store.cluster_members("solo", "https://solo.test/jobs/1") == []

    sizes = store.cluster_sizes([a, b, ("solo", "https://solo.test/jobs/1")])
    assert sizes == {a: 2, b: 2}  # standalone rows are simply absent

    stats = store.dedup_overlay_stats()
    assert stats == {
        "open_rows": 3,
        "open_clustered_rows": 2,
        "clusters_with_open": 1,
        "distinct_open": 2,
        "clusters": 1,
        "clustered_rows": 2,
    }


def test_new_positions_since_collapse_counts_jobs_not_rows(store) -> None:
    _pair(store)
    _sweep(store)
    since = "2000-01-01T00:00:00"
    assert store.new_positions_since(since) == 2
    assert store.new_positions_since(since, collapse=True) == 1
    # A window after both first_seen stamps counts nothing either way.
    assert store.new_positions_since("2999-01-01T00:00:00", collapse=True) == 0


# --- role groups: same role, several locations (presentation-only) --------------


def _role_groups(store: StateStore) -> dict[str, set[tuple[str, str]]]:
    rows = store._conn.execute(
        "SELECT site, url, group_id FROM position_role_groups"
    ).fetchall()
    out: dict[str, set[tuple[str, str]]] = {}
    for r in rows:
        out.setdefault(r["group_id"], set()).add((r["site"], r["url"]))
    return out


def _role_pair(store: StateStore) -> None:
    """One role (default company+title) open in two cities — the smallest group."""
    _put(store, "a", "https://a.test/1", location="New York, NY")
    _put(store, "b", "https://b.test/2", location="London")


def test_role_group_bundles_location_variants(store) -> None:
    _role_pair(store)
    _put(store, "c", "https://c.test/3", title="Data Engineer", location="Berlin")
    report = _sweep(store)

    groups = _role_groups(store)
    assert len(groups) == 1  # the lone Data Engineer never groups
    (members,) = groups.values()
    assert members == {("a", "https://a.test/1"), ("b", "https://b.test/2")}
    assert report["role_groups"] == 1
    assert report["role_grouped_rows"] == 2
    assert report["role_collapse_saving"] == 1
    assert report["guards"]["role_group_cap_skips"] == 0


def test_role_group_same_location_multiples_stay_ungrouped(store) -> None:
    # The robinhood shape: N identical-title reqs in ONE city are N separate
    # openings, not location variants — grouping them would hide real headcount.
    _put(store, "a", "https://a.test/1", location="Menlo Park, CA")
    _put(store, "b", "https://b.test/2", location="Menlo Park, CA")
    _put(store, "c", "https://c.test/3", location="menlo park, ca")  # case-folded
    _sweep(store)
    assert _role_groups(store) == {}


def test_role_group_buckets_read_normalized_never_display(store) -> None:
    # §4.12 froze this surface: the variant bucket stays location_normalized-else-raw.
    # These two rows RENDER identically ('Berlin, Germany' via location_display) but
    # their bucket keys differ, so they still form a group — the display summary is a
    # label, never a grouping key. Re-keying buckets onto the projection is an
    # explicit §14 Q10 decision, not a side effect.
    _put(store, "a", "https://a.test/1", location="Berlin")
    _put(store, "b", "https://b.test/2", location="Berlin, Germany")
    for site, url, loc in [
        ("a", "https://a.test/1", "Berlin"),
        ("b", "https://b.test/2", "Berlin, Germany"),
    ]:
        store.refresh_position_locations(
            site, url, location=loc, workplace="hybrid", force=True
        )
    displays = set()
    for site, url in [("a", "https://a.test/1"), ("b", "https://b.test/2")]:
        rec = store.get_position(site, url)
        assert rec is not None
        displays.add(rec.location_display)
    assert displays == {"Berlin, Germany"}  # identical rendering…
    _sweep(store)
    assert len(_role_groups(store)) == 1  # …still two buckets → a picker forms


def test_role_group_unlocated_rows_share_one_bucket(store) -> None:
    # No location evidence = no variant evidence: two blank locations are not
    # "two locations", so they never form a picker.
    _put(store, "a", "https://a.test/1", location="")
    _put(store, "b", "https://b.test/2", location="")
    _sweep(store)
    assert _role_groups(store) == {}


def test_role_group_degenerate_title_key_is_skipped(store) -> None:
    _put(store, "a", "https://a.test/1", title="", location="New York, NY")
    _put(store, "b", "https://b.test/2", title="", location="London")
    _sweep(store)
    assert _role_groups(store) == {}


def test_role_group_cap_guards_mass_posters(store) -> None:
    # 65 same-key rows is boilerplate titling, not one role in 65 cities.
    for i in range(65):
        _put(store, "s", f"https://s.test/{i}", location=f"City {i}")
    report = _sweep(store)
    assert _role_groups(store) == {}
    assert report["guards"]["role_group_cap_skips"] == 1
    assert report["role_groups"] == 0


def test_role_group_excludes_shadowed_copies_and_closed_rows(store) -> None:
    # Groups are keyed over what the collapsed list shows: a clustered duplicate
    # is already hidden, so bundling it too would double-count the role. Only the
    # cluster's canonical joins the standalone variant.
    _pair(store)  # board_a/board_b: one greenhouse req, location "Remote"
    _put(store, "board_c", "https://acme.test/careers/london-1", location="London")
    _sweep(store)

    (members,) = _role_groups(store).values()
    assert members == {
        ("board_b", "https://acme.test/careers?gh_jid=400123"),  # elected canonical
        ("board_c", "https://acme.test/careers/london-1"),
    }

    # One variant closing leaves a single open location: nothing left to pick.
    store.mark_closed(
        "board_c", "https://acme.test/careers/london-1", reason="missing"
    )
    _sweep(store)
    assert _role_groups(store) == {}


def test_role_groups_deterministic_and_disposable(store) -> None:
    _role_pair(store)
    _sweep(store)
    first = _role_groups(store)
    assert first  # sanity: the pair grouped
    _sweep(store)
    assert _role_groups(store) == first  # same group_id, same members
    _sweep(store, role_groups=False)  # knob off ⇒ the next sweep clears the table
    assert _role_groups(store) == {}


def test_role_group_variants_and_members_read_open_membership(store) -> None:
    _role_pair(store)
    _put(store, "c", "https://c.test/3", company="Other Co", location="Berlin")
    _sweep(store)

    variants = store.role_group_variants(
        [("a", "https://a.test/1"), ("c", "https://c.test/3")]
    )
    assert set(variants) == {("a", "https://a.test/1")}  # ungrouped row absent
    got = variants[("a", "https://a.test/1")]
    assert {v["location"] for v in got} == {"New York, NY", "London"}  # self included

    members = store.role_group_members("b", "https://b.test/2")
    assert {(m.site, m.url) for m in members} == {
        ("a", "https://a.test/1"),
        ("b", "https://b.test/2"),
    }
    assert store.role_group_members("c", "https://c.test/3") == []


# --- role groups: query-time primary election ------------------------------------


def test_role_collapse_surfaces_the_best_scored_variant(store) -> None:
    _role_pair(store)
    _sweep(store)
    store.set_match("a", "https://a.test/1", score=60, verdict="good", reasons="[]")
    store.set_match("b", "https://b.test/2", score=85, verdict="strong", reasons="[]")

    assert _urls(store.search_positions(collapse_role_groups=True, limit=100)) == {
        "https://b.test/2"
    }
    # Verdicts land between sweeps; election is query-time so the surfaced
    # variant follows them immediately, no rebuild needed.
    store.set_match("a", "https://a.test/1", score=95, verdict="strong", reasons="[]")
    assert _urls(store.search_positions(collapse_role_groups=True, limit=100)) == {
        "https://a.test/1"
    }


def test_role_collapse_prefers_any_score_over_none_then_ties_deterministically(
    store,
) -> None:
    _role_pair(store)
    _sweep(store)
    # Unscored ties (NULL == NULL) break on first_seen then (site, url): "a" wins.
    assert _urls(store.search_positions(collapse_role_groups=True, limit=100)) == {
        "https://a.test/1"
    }
    # A score of 0 still outranks "never scored" — NULL sorts below everything.
    store.set_match("b", "https://b.test/2", score=0, verdict="reject", reasons="[]")
    assert _urls(store.search_positions(collapse_role_groups=True, limit=100)) == {
        "https://b.test/2"
    }


def test_role_collapse_reacts_to_closure_without_a_resweep(store) -> None:
    _role_pair(store)
    _sweep(store)
    store.set_match("b", "https://b.test/2", score=85, verdict="strong", reasons="[]")
    assert _urls(store.search_positions(collapse_role_groups=True, limit=100)) == {
        "https://b.test/2"
    }

    store.mark_closed("b", "https://b.test/2", reason="missing")
    # No sweep ran: the stale overlay row must not keep hiding the survivor…
    assert _urls(
        store.search_positions(status="open", collapse_role_groups=True, limit=100)
    ) == {"https://a.test/1"}
    # …and a closed row itself never role-collapses (the both-states view keeps it).
    assert "https://b.test/2" in _urls(
        store.search_positions(collapse_role_groups=True, limit=100)
    )


def test_role_collapse_bypassed_on_user_data_queries(store) -> None:
    _role_pair(store)
    _sweep(store)
    store.set_match("b", "https://b.test/2", score=85, verdict="strong", reasons="[]")
    store.set_dismissed("a", "https://a.test/1", True)

    # The dismissed view must keep the operator's own action reachable even
    # though a better-scored sibling would normally shadow the row.
    got = store.search_positions(dismissed=True, collapse_role_groups=True, limit=100)
    assert _urls(got) == {"https://a.test/1"}


def test_role_collapse_off_is_a_no_op(store) -> None:
    _role_pair(store)
    _sweep(store)
    plain = _urls(store.search_positions(limit=100))
    assert plain == {"https://a.test/1", "https://b.test/2"}  # flag unset: raw rows
