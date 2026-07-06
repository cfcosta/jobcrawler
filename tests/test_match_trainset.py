"""Projecting persisted match verdicts + operator feedback into a matcher trainset (§8)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from jobcrawler.match import _content_hash, _digest, candidate_from_row
from jobcrawler.match_trainset import (
    MATCH_INPUT_FIELDS,
    build_examples,
    corrected_label,
    load_trainset,
    save_trainset,
)
from jobcrawler.state import StateStore

_ENGAGED_URL = "https://acme.example/jobs/engaged"


def _seed_scored(
    store: StateStore,
    *,
    url: str,
    score: int,
    verdict: str,
    reasons: str = "[]",
    concerns: str = "[]",
    description: str = "We build distributed payment systems in Rust.",
    title: str = "Senior Backend Engineer",
    company: str = "Acme",
    track_status: str | None = None,
    dismissed: bool = False,
) -> None:
    """Upsert one scored row the way the runner + matcher would, then apply operator state."""
    job = SimpleNamespace(
        title=title,
        company=company,
        location="Remote",
        description=description,
        employment_type="full-time",
        department="Engineering",
    )
    fields: dict[str, object] = {
        "title": title,
        "company": company,
        "location": "Remote",
        "discipline": "backend",
        "level": "senior",
        "workplace": "remote",
        "employment_type": "full-time",
        "department": "Engineering",
    }
    if description:
        fields["description"] = description
    store.upsert_position("acme", url, fields=fields, content_hash=_content_hash(job))
    store.set_match(
        "acme", url, score=score, verdict=verdict, reasons=reasons, concerns=concerns
    )
    if track_status:
        store.set_track_status(
            "acme",
            url,
            track_status,
            stamp_applied=track_status
            in {"applied", "interviewing", "offer", "accepted"},
        )
    if dismissed:
        store.set_dismissed("acme", url, True)


def _scored_rows(store: StateStore) -> list:
    return store.search_positions(
        min_score=0, status=None, dismissed=None, sort="score", limit=1000
    )


# --- feedback correction (unit) ---------------------------------------------


def test_corrected_label_keeps_the_llm_verdict_without_feedback() -> None:
    rec = SimpleNamespace(
        match_score=72,
        match_verdict="good",
        match_reasons='["knows rust"]',
        match_concerns='["maybe onsite"]',
        track_status="new",
        dismissed=False,
    )
    label = corrected_label(rec)
    assert label == {
        "index": 0,
        "score": 72,
        "verdict": "good",
        "reasons": ["knows rust"],
        "concerns": ["maybe onsite"],
    }


def test_corrected_label_dismissed_becomes_a_hard_reject() -> None:
    # The operator dismissing a strong LLM verdict is the correction the whole feature exists
    # to capture: the label must flip to reject, not keep the model's score.
    rec = SimpleNamespace(
        match_score=91,
        match_verdict="strong",
        match_reasons='["great fit"]',
        match_concerns="[]",
        track_status="new",
        dismissed=True,
    )
    label = corrected_label(rec)
    assert label["score"] == 10
    assert label["verdict"] == "reject"
    assert label["concerns"]  # a rationale trace is always present


def test_corrected_label_rejected_status_becomes_reject() -> None:
    rec = SimpleNamespace(
        match_score=80,
        match_verdict="strong",
        match_reasons="[]",
        match_concerns="[]",
        track_status="rejected",
        dismissed=False,
    )
    assert corrected_label(rec)["verdict"] == "reject"


def test_corrected_label_engaged_status_becomes_strong() -> None:
    # A lukewarm LLM score the operator then applied to is a strong match by revealed pref.
    rec = SimpleNamespace(
        match_score=40,
        match_verdict="weak",
        match_reasons="[]",
        match_concerns="[]",
        track_status="applied",
        dismissed=False,
    )
    label = corrected_label(rec)
    assert label["score"] == 85  # max(40, 85)
    assert label["verdict"] == "strong"
    assert label["reasons"]


def test_corrected_label_none_when_unscored() -> None:
    rec = SimpleNamespace(
        match_score=None,
        match_verdict=None,
        match_reasons=None,
        match_concerns=None,
        track_status="new",
        dismissed=False,
    )
    assert corrected_label(rec) is None


def test_corrected_label_tolerates_garbage_reason_json() -> None:
    rec = SimpleNamespace(
        match_score=55,
        match_verdict="good",
        match_reasons="not json",
        match_concerns=None,
        track_status="new",
        dismissed=False,
    )
    label = corrected_label(rec)
    assert label["reasons"] == [] and label["concerns"] == []


# --- end-to-end projection through a real store ------------------------------


def test_build_examples_projects_a_scored_row() -> None:
    store = StateStore(":memory:")
    _seed_scored(
        store,
        url="https://acme.example/jobs/1",
        score=72,
        verdict="good",
        reasons='["knows rust"]',
        concerns='["maybe onsite"]',
    )
    (example,) = build_examples(_scored_rows(store), "PROFILE BODY")

    assert example.profile == "PROFILE BODY"
    assert set(example.inputs().keys()) == set(MATCH_INPUT_FIELDS)
    verdicts = json.loads(example.verdicts)
    assert verdicts[0]["score"] == 72
    assert verdicts[0]["verdict"] == "good"
    assert "knows rust" in verdicts[0]["reasons"]
    assert "maybe onsite" in verdicts[0]["concerns"]
    # the persisted description rides into the digest the live matcher would have sent
    assert "distributed payment systems" in example.jobs
    store.close()


def test_jobs_input_is_byte_identical_to_the_production_digest() -> None:
    # If the trainset's `jobs` string ever drifts from what the live matcher sends, we would
    # be distilling on an input distribution the model never sees. Pin it exactly.
    store = StateStore(":memory:")
    _seed_scored(store, url="https://acme.example/jobs/1", score=72, verdict="good")
    (rec,) = _scored_rows(store)
    (example,) = build_examples([rec], "BODY")

    enriched = candidate_from_row(rec)
    assert example.jobs == json.dumps([_digest(0, enriched)], ensure_ascii=False)
    store.close()


def test_build_examples_applies_feedback_end_to_end() -> None:
    store = StateStore(":memory:")
    _seed_scored(
        store, url=_ENGAGED_URL, score=40, verdict="weak", track_status="applied"
    )
    _seed_scored(
        store,
        url="https://acme.example/jobs/dismissed",
        score=90,
        verdict="strong",
        dismissed=True,
    )
    bands = {
        json.loads(ex.verdicts)[0]["verdict"]
        for ex in build_examples(_scored_rows(store), "BODY")
    }
    # the applied row lifts to strong, the dismissed row drops to reject — both corrections
    # land, overruling the raw 40/90 LLM scores.
    assert bands == {"strong", "reject"}
    store.close()


def test_unscored_rows_never_leak_into_the_trainset() -> None:
    store = StateStore(":memory:")
    _seed_scored(store, url="https://acme.example/jobs/scored", score=66, verdict="good")
    # an open, never-matched row: no set_match at all
    store.upsert_position(
        "acme",
        "https://acme.example/jobs/unscored",
        fields={
            "title": "Other",
            "company": "Other Co",
            "location": "Remote",
            "description": "desc",
        },
        content_hash="whatever",
    )
    examples = build_examples(_scored_rows(store), "BODY")
    assert len(examples) == 1
    store.close()


def test_unscorable_scored_row_is_skipped() -> None:
    # A scored row that predates the description column can't be re-presented host-side; it
    # must drop from the trainset (not emit an example with an empty digest).
    store = StateStore(":memory:")
    _seed_scored(
        store,
        url="https://acme.example/jobs/nodesc",
        score=55,
        verdict="good",
        description="",
    )
    assert build_examples(_scored_rows(store), "BODY") == []
    store.close()


# --- persistence roundtrip ---------------------------------------------------


def test_save_and_load_roundtrips(tmp_path) -> None:
    store = StateStore(":memory:")
    _seed_scored(
        store,
        url="https://acme.example/jobs/1",
        score=72,
        verdict="good",
        reasons='["knows rust"]',
    )
    examples = build_examples(_scored_rows(store), "PROFILE BODY")
    path = str(tmp_path / "match-trainset.jsonl")
    save_trainset(examples, path)

    loaded = load_trainset(path)
    assert len(loaded) == 1
    assert loaded[0].profile == "PROFILE BODY"
    assert loaded[0].jobs == examples[0].jobs
    assert loaded[0].verdicts == examples[0].verdicts
    assert set(loaded[0].inputs().keys()) == set(MATCH_INPUT_FIELDS)
    store.close()


def test_save_trainset_writes_one_json_object_per_line(tmp_path) -> None:
    store = StateStore(":memory:")
    _seed_scored(store, url="https://acme.example/jobs/1", score=72, verdict="good")
    _seed_scored(store, url="https://acme.example/jobs/2", score=61, verdict="good")
    examples = build_examples(_scored_rows(store), "BODY")
    path = str(tmp_path / "t.jsonl")
    save_trainset(examples, path)

    lines = [ln for ln in open(path).read().splitlines() if ln.strip()]
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert set(row.keys()) == {"profile", "jobs", "verdicts"}
    store.close()


def test_save_empty_trainset_is_empty_file(tmp_path) -> None:
    path = str(tmp_path / "empty.jsonl")
    save_trainset([], path)
    assert open(path).read() == ""
    assert load_trainset(path) == []
