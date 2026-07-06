"""Tests for the crawl runner (§4.7) — the pipeline heart: crawl → track → match.

The most load-bearing tests in the repo. They drive the real import → crawl →
validate → completeness/expiry floors → content-hash → lifecycle upsert → enrich →
prefilter → cached matching → notify path against inline ``Job`` crawlers (no
network; ctx is a MockTransport that is never hit unless a test fetches). Matching
uses the *real* :class:`Matcher` with its ``_predict`` swapped for a fake so no LLM
is called, exactly as ``test_match.py`` does.

The closure-diff tests are the correctness core: the §4.7 mass-closure guard must age
out genuinely-gone positions on a healthy full-enumeration crawl, yet NEVER close the
book on a site's positions when the crawl was partial (raised), empty (yielded 0), or
windowed (``enumerates_all=False``). A broken crawl must degrade to "found nothing new",
never to "closed everything".
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast

import httpx
from pytest import MonkeyPatch

from jobcrawler.match import Matcher
from jobcrawler.profile import Profile, ProfilePrefs
from jobcrawler.runtime.contract import Ctx as CtxProtocol
from jobcrawler.runtime.contract import Job
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.runner import (
    crawl_site,
    crawl_site_isolated,
    probe_crawler,
    probe_isolated,
)
from jobcrawler.state import StateStore
from jobcrawler.urls import canonicalize_url

UTC = timezone.utc

Handler = Callable[[httpx.Request], httpx.Response]
CrawlFn = Callable[[CtxProtocol], Iterable[Job]]


# --- fixtures / builders ---------------------------------------------------


def _ctx(handler: Handler | None = None, *, max_pages: int | None = None) -> Ctx:
    handler = handler or (lambda r: httpx.Response(404))
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    if max_pages is not None:
        return Ctx({"example.com"}, client=client, max_pages=max_pages)
    return Ctx({"example.com"}, client=client)


def _job(url: str = "https://example.com/a", **kw: object) -> Job:
    """A complete §4.6 ``Job`` with sensible engineering defaults; override via kwargs.

    Default title classifies to ``(backend, senior, remote)`` and the description clears
    a 300-char floor, so a job passes the completeness floor and an empty-prefs prefilter
    unless a test deliberately makes it short / off-discipline / keyword-excluded.
    """
    fields: dict[str, object] = {
        "url": url,
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "We are hiring a backend engineer to build services. " * 10,
        "source": "example",
    }
    fields.update(kw)
    return Job.model_validate(fields)


def _crawl(*jobs: Job) -> CrawlFn:
    """A crawler that yields exactly ``jobs`` (no fetch); ``_crawl()`` yields nothing."""

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield from jobs

    return crawl


def _crawl_then_raise(*jobs: Job) -> CrawlFn:
    """A crawler that yields ``jobs`` then raises mid-crawl (partial enumeration)."""

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield from jobs
        raise RuntimeError("boom mid-crawl")

    return crawl


def _open_urls(store: StateStore, site: str) -> set[str]:
    return {p.url for p in store.open_positions(site)}


# -- hermetic matcher (real Matcher, fake predictor; mirrors test_match.py) --


class _FakePredictor:
    """Stands in for ``dspy.Predict(MatchJobs)``: records calls, returns canned verdicts."""

    def __init__(self, respond: Callable[[list[dict[str, object]]], str]) -> None:
        self.calls = 0
        self.batches: list[list[dict[str, object]]] = []
        self._respond = respond

    def __call__(self, *, profile: str, jobs: str) -> SimpleNamespace:
        self.calls += 1
        parsed = cast("list[dict[str, object]]", json.loads(jobs))
        self.batches.append(parsed)
        return SimpleNamespace(verdicts=self._respond(parsed))


class _FakeGovernor:
    def __init__(self) -> None:
        self.paces = 0

    def pace(self) -> None:
        self.paces += 1


def _clock() -> datetime:
    return datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _all_score(score: int) -> Callable[[list[dict[str, object]]], str]:
    def respond(jobs: list[dict[str, object]]) -> str:
        return json.dumps(
            [
                {
                    "index": j["index"],
                    "score": score,
                    "verdict": "",
                    "reasons": ["fits the stack"],
                    "concerns": [],
                }
                for j in jobs
            ]
        )

    return respond


def _score_by_title(
    table: dict[str, int],
) -> Callable[[list[dict[str, object]]], str]:
    def respond(jobs: list[dict[str, object]]) -> str:
        return json.dumps(
            [
                {
                    "index": j["index"],
                    "score": table[cast(str, j["title"])],
                    "verdict": "",
                    "reasons": ["r"],
                    "concerns": [],
                }
                for j in jobs
            ]
        )

    return respond


def _profile(**prefs_kw: object) -> Profile:
    prefs = ProfilePrefs(
        disciplines=frozenset(),
        levels=frozenset(),
        workplace=frozenset(),
        locations=(),
        min_salary=None,
        languages=(),
        exclude_keywords=(),
        exclude_companies=(),
        exclude_locations=(),
    )
    if prefs_kw:
        prefs = replace(prefs, **prefs_kw)  # type: ignore[arg-type]
    return Profile(
        raw="body", body="a backend engineer", prefs=prefs, profile_hash="ph"
    )


def _matcher(
    store: StateStore,
    respond: Callable[[list[dict[str, object]]], str],
    *,
    batch: int = 10,
    daily_cap: int = 1000,
    profile: Profile | None = None,
) -> Matcher:
    m = Matcher(
        store,
        profile or _profile(),
        model="openrouter/test/model",
        batch=batch,
        daily_cap=daily_cap,
        governor=_FakeGovernor(),
        clock=_clock,
    )
    # Swap the dspy predictor for a fake (monkeypatch via setattr so no LLM is bound).
    setattr(m, "_predict", _FakePredictor(respond))
    return m


def _capture_notify(
    monkeypatch: MonkeyPatch,
) -> list[tuple[str, str, dict[str, object]]]:
    events: list[tuple[str, str, dict[str, object]]] = []

    def rec(event: str, message: str, **fields: object) -> None:
        events.append((event, message, fields))

    monkeypatch.setattr("jobcrawler.notify.notify", rec)
    return events


# --- crawl → validate → track ----------------------------------------------


def test_crawl_tracks_each_position() -> None:
    store = StateStore(":memory:")
    summary = crawl_site(
        "s",
        _crawl(_job("https://example.com/a"), _job("https://example.com/b")),
        _ctx(),
        store,
    )
    assert (summary.positions_found, summary.new_positions, summary.errors) == (2, 2, 0)
    assert summary.changed_positions == 0
    assert len(store.open_positions("s")) == 2
    store.close()


def test_second_run_dedups_unchanged() -> None:
    store = StateStore(":memory:")
    jobs = (_job("https://example.com/a"), _job("https://example.com/b"))
    crawl_site("s", _crawl(*jobs), _ctx(), store)
    second = crawl_site("s", _crawl(*jobs), _ctx(), store)
    assert second.positions_found == 2  # still enumerated
    assert (second.new_positions, second.changed_positions) == (0, 0)  # nothing new
    store.close()


def test_edited_content_is_tracked_as_changed() -> None:
    # A posting whose description changes re-hashes → 'changed' (re-surfaced, re-matched).
    store = StateStore(":memory:")
    crawl_site(
        "s", _crawl(_job("https://example.com/a", description="x" * 400)), _ctx(), store
    )
    second = crawl_site(
        "s", _crawl(_job("https://example.com/a", description="y" * 400)), _ctx(), store
    )
    assert (second.new_positions, second.changed_positions) == (0, 1)
    store.close()


def test_run_history_is_recorded() -> None:
    store = StateStore(":memory:")
    crawl_site(
        "s",
        _crawl(_job("https://example.com/a"), _job("https://example.com/b")),
        _ctx(),
        store,
    )
    rec = store.last_run("s")
    assert rec is not None and rec.finished is not None
    assert (rec.items_found, rec.new_items) == (2, 2)
    store.close()


def test_script_exception_is_isolated() -> None:
    store = StateStore(":memory:")
    summary = crawl_site(
        "s", _crawl_then_raise(_job("https://example.com/a")), _ctx(), store
    )
    assert summary.positions_found == 1  # the one yielded before the raise
    assert summary.new_positions == 1  # still tracked
    assert summary.errors == 1  # the raise, a health signal not a crash
    store.close()


def test_non_job_yield_counts_as_error() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield _job("https://example.com/a")
        yield cast(Job, "not a job")  # runtime non-Job exercises the guard

    store = StateStore(":memory:")
    summary = crawl_site("s", crawl, _ctx(), store)
    assert summary.positions_found == 1
    assert summary.errors == 1
    store.close()


def test_dict_yield_is_validated_and_tracked() -> None:
    # Generated crawlers commonly yield plain dicts; the runner must accept them
    # (validating into Job) exactly as the sandbox does — else validate passes but crawl
    # fails.
    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield cast(
            Job,
            {
                "url": "https://example.com/a",
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Remote",
                "description": "x" * 50,
                "source": "example",
            },
        )

    store = StateStore(":memory:")
    summary = crawl_site("s", crawl, _ctx(), store)
    assert (summary.positions_found, summary.new_positions, summary.errors) == (1, 1, 0)
    assert store.open_positions("s")[0].title == "Backend Engineer"
    store.close()


# --- completeness + expired floors -----------------------------------------


def test_incomplete_descriptions_are_not_tracked() -> None:
    # "Only track complete" (§4.6): a stub/truncated description is dropped host-side,
    # whatever the (untrusted) crawler yields.
    store = StateStore(":memory:")
    summary = crawl_site(
        "s",
        _crawl(
            _job("https://example.com/stub", description="too short"),
            _job("https://example.com/full", description="x" * 400),
        ),
        _ctx(),
        store,
        min_description_chars=300,
    )
    assert summary.new_positions == 1  # only the complete posting tracked
    assert summary.incomplete == 1  # the stub, surfaced for visibility
    assert _open_urls(store, "s") == {canonicalize_url("https://example.com/full")}
    store.close()


def test_run_record_carries_body_quality_signal() -> None:
    # §4.8 partial-drift telemetry (P0): the run persists body_quality under that exact
    # key (the landed health/metrics consumers read it) so a site that starts serving
    # stubs is spotted even while positions_found stays healthy.
    store = StateStore(":memory:")
    crawl_site(
        "s",
        _crawl(
            _job("https://example.com/stub", description="too short"),
            _job("https://example.com/full", description="x" * 400),
        ),
        _ctx(),
        store,
        min_description_chars=300,
    )
    rec = store.last_run("s")
    assert rec is not None and rec.signals is not None
    assert rec.signals["body_quality"] == 0.5  # 1 of 2 complete
    assert rec.signals["incomplete"] == 1
    store.close()


def test_incomplete_position_stays_untracked_for_re_evaluation() -> None:
    # A stub is not tracked, so once the page carries the full description the next crawl
    # tracks it — no permanent drop.
    served = {"description": "still just a stub"}

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield _job("https://example.com/a", description=served["description"])

    store = StateStore(":memory:")
    first = crawl_site("s", crawl, _ctx(), store, min_description_chars=300)
    assert (first.new_positions, first.incomplete) == (0, 1)
    assert store.open_positions("s") == []  # left untracked

    served["description"] = "x" * 400  # page now serves the full posting
    second = crawl_site("s", crawl, _ctx(), store, min_description_chars=300)
    assert second.new_positions == 1
    assert len(store.open_positions("s")) == 1
    store.close()


def test_no_floor_tracks_everything() -> None:
    # Default (min_description_chars=0) is off: a short description still tracks, so the
    # runner's other callers are unchanged unless completeness is wired.
    store = StateStore(":memory:")
    summary = crawl_site(
        "s", _crawl(_job("https://example.com/a", description="x")), _ctx(), store
    )
    assert (summary.new_positions, summary.incomplete) == (1, 0)
    store.close()


def test_expired_marker_drops_and_closes() -> None:
    # An expired-marker posting is never tracked/matched, and force-closes any open row
    # for it — independently of the closure diff.
    store = StateStore(":memory:")
    markers = ("position filled",)
    url = "https://example.com/a"
    canon = canonicalize_url(url)
    crawl_site("s", _crawl(_job(url)), _ctx(), store, expired_markers=markers)
    assert canon in _open_urls(store, "s")

    summary = crawl_site(
        "s",
        _crawl(_job(url, description="This position filled last week. " + "x" * 400)),
        _ctx(),
        store,
        expired_markers=markers,
    )
    assert summary.positions_found == 1  # a valid Job was yielded
    assert (summary.new_positions, summary.changed_positions, summary.incomplete) == (
        0,
        0,
        0,
    )
    assert summary.closed_positions == 0  # closed by the marker, not the diff
    counts = store.position_counts()
    assert (counts["open"], counts["closed"]) == (0, 1)
    store.close()


# --- closure diff (§4.7 mass-closure guard) --------------------------------


def test_closure_happy_path_closes_absent_positions() -> None:
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open
    summary = crawl_site("s", _crawl(a), _ctx(), store, close_after_misses=1)  # B gone
    assert summary.closed_positions == 1
    assert _open_urls(store, "s") == {canonicalize_url("https://example.com/a")}
    store.close()


def test_closure_skipped_on_exception_run() -> None:
    # A crawl that raises enumerated only partially: the unseen tail must NOT be closed.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open
    summary = crawl_site("s", _crawl_then_raise(a), _ctx(), store, close_after_misses=1)
    assert summary.errors == 1
    assert summary.closed_positions == 0  # guard: crawl raised → no diff-close
    assert _open_urls(store, "s") == {
        canonicalize_url("https://example.com/a"),
        canonicalize_url("https://example.com/b"),
    }  # B still open despite being absent
    store.close()


def test_closure_skipped_on_zero_yield_run() -> None:
    # THE correctness invariant: a broken crawl yielding 0 must never close the book on a
    # site's positions.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open
    summary = crawl_site("s", _crawl(), _ctx(), store, close_after_misses=1)  # yields 0
    assert summary.positions_found == 0  # a blackout
    assert summary.closed_positions == 0  # guard: nothing present → no diff-close
    assert len(store.open_positions("s")) == 2  # both survive
    store.close()


def test_closure_skipped_when_not_enumerating() -> None:
    # A windowed source (enumerates_all=False, e.g. a "latest 20" feed) never diff-closes;
    # a §4.9 per-URL recheck verifies its closures instead.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open (full enumeration)
    summary = crawl_site(
        "s", _crawl(a), _ctx(), store, enumerates_all=False, close_after_misses=1
    )
    assert (
        summary.closed_positions == 0
    )  # guard: not a full enumeration → no diff-close
    assert len(store.open_positions("s")) == 2  # B survives (would need a recheck)
    store.close()


def test_closure_skipped_under_the_enumeration_floor() -> None:
    # A board whose known total is 10 comes back with just 1 alive — a slice, not the
    # board (broken pagination / drifted selector), though the crawl never raised. Closing
    # the tail against that slice would false-close the 9 it missed, so the floor (§4.7)
    # skips the diff exactly as a raised crawl does.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open
    summary = crawl_site(
        "s", _crawl(a), _ctx(), store, expected_positions=10, close_after_misses=1
    )
    assert summary.closed_positions == 0  # 1 of 10 alive is under the 0.5 floor → skip
    assert _open_urls(store, "s") == {
        canonicalize_url("https://example.com/a"),
        canonicalize_url("https://example.com/b"),
    }  # B survives despite being absent
    rec = store.last_run("s")
    assert rec is not None and rec.signals is not None
    assert rec.signals["under_enumerated"] == {"seen": 1, "floor": 5, "expected": 10}
    store.close()


def test_closure_runs_when_the_enumeration_floor_is_met() -> None:
    # The same board comes back with 5 of its ~10 alive — clears the 0.5 floor, so the run
    # counts as a healthy enumeration and the one absent role IS aged out. The floor gates
    # only slices, never a board that legitimately shrank within tolerance.
    store = StateStore(":memory:")
    urls = [f"https://example.com/{n}" for n in range(6)]
    jobs = [_job(u) for u in urls]
    crawl_site("s", _crawl(*jobs), _ctx(), store, expected_positions=10)  # 6 open
    summary = crawl_site(
        "s",
        _crawl(*jobs[:5]),  # drop the 6th → 5 alive, clears floor(5)
        _ctx(),
        store,
        expected_positions=10,
        close_after_misses=1,
    )
    assert summary.closed_positions == 1  # the dropped role ages out normally
    assert canonicalize_url(urls[5]) not in _open_urls(store, "s")
    store.close()


def test_enumeration_floor_dormant_without_an_estimate() -> None:
    # expected_positions defaults to 0 (no estimate) — the floor never fires, so a board
    # that legitimately shrank to 1 of 2 still closes the absent role. Guards the 216
    # existing registry entries against a behaviour change.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    crawl_site("s", _crawl(a, b), _ctx(), store)  # A, B open, no estimate
    summary = crawl_site("s", _crawl(a), _ctx(), store, close_after_misses=1)  # B gone
    assert summary.closed_positions == 1  # floor dormant → normal closure
    rec = store.last_run("s")
    assert rec is not None and rec.signals is not None
    assert "under_enumerated" not in rec.signals
    store.close()


def test_reopen_flow_resurfaces_a_closed_position() -> None:
    # A closed posting that reappears REOPENS as 'changed' (verdict intact — the store's
    # job), so a churned-then-returned role is re-surfaced without re-billing matching.
    store = StateStore(":memory:")
    a, b = _job("https://example.com/a"), _job("https://example.com/b")
    canon_a = canonicalize_url("https://example.com/a")
    crawl_site("s", _crawl(a), _ctx(), store)  # A open
    crawl_site("s", _crawl(b), _ctx(), store, close_after_misses=1)  # A absent → closed
    assert canon_a not in _open_urls(store, "s")

    third = crawl_site("s", _crawl(a), _ctx(), store, close_after_misses=1)  # A returns
    assert third.changed_positions == 1  # reopened, reported as changed
    assert canon_a in _open_urls(store, "s")
    store.close()


def test_a_truncated_reserve_is_not_closed_by_the_diff() -> None:
    # finding 2: the completeness floor drops a momentarily-truncated posting from tracking,
    # but the runner still SAW it served ALIVE. On a healthy full-enumeration crawl the
    # closure diff must count it as seen (never age it toward closure). Otherwise the floor
    # punches a hole in the closure set and buries a live posting that merely truncated once.
    store = StateStore(":memory:")
    a_full = _job("https://example.com/a", description="x" * 400)
    b_full = _job("https://example.com/b", description="y" * 400)
    crawl_site("s", _crawl(a_full, b_full), _ctx(), store, min_description_chars=300)
    canon_a = canonicalize_url("https://example.com/a")
    canon_b = canonicalize_url("https://example.com/b")
    assert _open_urls(store, "s") == {canon_a, canon_b}

    # crawl 2: A still complete (present → the guard fires), B served but truncated below the
    # floor. B is LIVE, not gone — it is in ``seen`` though not ``present``.
    b_stub = _job("https://example.com/b", description="temporarily truncated")
    summary = crawl_site(
        "s",
        _crawl(a_full, b_stub),
        _ctx(),
        store,
        min_description_chars=300,
        close_after_misses=1,
    )
    assert summary.incomplete == 1  # B surfaced incomplete
    assert summary.closed_positions == 0  # guard fired (A present) yet B counts as seen
    assert _open_urls(store, "s") == {canon_a, canon_b}  # B survives the diff
    store.close()


# --- matching (§6/§8): prefilter → cached scoring → notify -----------------


def test_matcher_none_tracks_without_matching() -> None:
    # The Optional-matcher contract: None means crawl + track WITHOUT matching, never a
    # crash and never a pending flag.
    store = StateStore(":memory:")
    summary = crawl_site(
        "s",
        _crawl(_job("https://example.com/a"), _job("https://example.com/b")),
        _ctx(),
        store,
        matcher=None,
    )
    assert (summary.new_positions, summary.matched, summary.match_pending) == (2, 0, 0)
    assert store.position_counts()["matched"] == 0
    store.close()


def test_match_summary_counters_and_persist(monkeypatch: MonkeyPatch) -> None:
    events = _capture_notify(monkeypatch)
    store = StateStore(":memory:")
    matcher = _matcher(store, _all_score(85))
    summary = crawl_site(
        "s",
        _crawl(_job("https://example.com/a"), _job("https://example.com/b")),
        _ctx(),
        store,
        matcher=matcher,
    )
    assert (summary.matched, summary.match_pending) == (2, 0)
    assert len(store.matches(70)) == 2  # both persisted, open, >= threshold
    assert store.position_counts()["matched"] == 2
    assert len(events) == 2  # both strong (85 >= 70) → notified
    assert all(ev[0] == "match" for ev in events)
    store.close()


def test_only_strong_matches_notify(monkeypatch: MonkeyPatch) -> None:
    events = _capture_notify(monkeypatch)
    store = StateStore(":memory:")
    strong = _job("https://example.com/strong", title="Senior Backend Engineer")
    weak = _job("https://example.com/weak", title="Junior Backend Engineer")
    matcher = _matcher(
        store,
        _score_by_title({"Senior Backend Engineer": 85, "Junior Backend Engineer": 50}),
    )
    summary = crawl_site("s", _crawl(strong, weak), _ctx(), store, matcher=matcher)

    assert summary.matched == 2  # both scored and persisted
    assert len(events) == 1  # but only the strong one notified (weak 50 < 70)
    _event, _msg, fields = events[0]
    assert fields["site"] == "s"
    assert fields["url"] == canonicalize_url("https://example.com/strong")
    assert fields["title"] == "Senior Backend Engineer"
    assert fields["company"] == "Acme"
    assert fields["score"] == 85
    assert isinstance(fields["reasons"], str)
    store.close()


def test_prefilter_drops_before_scoring() -> None:
    # An excluded-keyword posting is tracked but never reaches the LLM (§6 token economy).
    store = StateStore(":memory:")
    keep = _job("https://example.com/keep", title="Senior Backend Engineer")
    drop = _job("https://example.com/drop", title="Backend Intern")
    matcher = _matcher(
        store, _all_score(85), profile=_profile(exclude_keywords=("intern",))
    )
    summary = crawl_site("s", _crawl(keep, drop), _ctx(), store, matcher=matcher)

    assert summary.matched == 1  # only the kept posting was scored
    predictor = cast(_FakePredictor, matcher._predict)
    assert len(predictor.batches) == 1 and len(predictor.batches[0]) == 1
    assert len(store.open_positions("s")) == 2  # both still TRACKED
    assert store.position_counts()["matched"] == 1
    store.close()


def test_unchanged_positions_are_not_rescored() -> None:
    # Only new/changed positions reach the matcher; an unchanged re-crawl re-bills nothing.
    store = StateStore(":memory:")
    jobs = (_job("https://example.com/a"), _job("https://example.com/b"))
    matcher = _matcher(store, _all_score(85))
    first = crawl_site("s", _crawl(*jobs), _ctx(), store, matcher=matcher)
    assert first.matched == 2
    predictor = cast(_FakePredictor, matcher._predict)
    assert predictor.calls == 1

    second = crawl_site("s", _crawl(*jobs), _ctx(), store, matcher=matcher)
    assert (second.matched, second.match_pending) == (0, 0)  # nothing new to score
    assert predictor.calls == 1  # matcher never re-invoked
    store.close()


def test_first_crawl_cap_scores_newest_and_parks_the_rest() -> None:
    # On a site's first crawl the cap bounds only MATCHING: every posting is tracked, but
    # only the freshest K are scored now and the tail is parked match_pending.
    store = StateStore(":memory:")

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        for i in range(5):
            yield _job(
                f"https://example.com/{i}",
                title=f"T{i} Backend Engineer",
                posted_at=datetime(2026, 1, 1 + i, tzinfo=UTC),
            )

    matcher = _matcher(store, _all_score(85))
    summary = crawl_site("s", crawl, _ctx(), store, matcher=matcher, first_crawl_cap=2)

    assert summary.positions_found == 5
    assert len(store.open_positions("s")) == 5  # ALL tracked, cap is matching-only
    assert (summary.matched, summary.match_pending) == (
        2,
        3,
    )  # newest 2 scored, 3 parked
    predictor = cast(_FakePredictor, matcher._predict)
    scored_titles = {cast(str, j["title"]) for j in predictor.batches[0]}
    assert scored_titles == {"T4 Backend Engineer", "T3 Backend Engineer"}  # freshest
    assert len(store.pending_matches(limit=10)) == 3
    store.close()


def test_parked_backlog_drains_on_a_later_crawl() -> None:
    # finding 1: the first-crawl / daily cap parks a never-scored tail match_pending. A later
    # crawl of the SAME (unchanged) postings must AUTOMATICALLY drain that backlog — nothing
    # else does (rematch is cache-only, so it can't score a never-scored job; recheck only
    # verifies closures). Without the drain the tail starves match_pending forever.
    store = StateStore(":memory:")

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        for i in range(5):
            yield _job(
                f"https://example.com/{i}",
                title=f"T{i} Backend Engineer",
                posted_at=datetime(2026, 1, 1 + i, tzinfo=UTC),
            )

    matcher = _matcher(store, _all_score(85))
    first = crawl_site("s", crawl, _ctx(), store, matcher=matcher, first_crawl_cap=2)
    assert (first.matched, first.match_pending) == (2, 3)  # newest 2 scored, 3 parked
    assert len(store.pending_matches(limit=10)) == 3

    # a later (non-first) crawl re-serves the SAME postings unchanged → the 3 parked drain.
    second = crawl_site("s", crawl, _ctx(), store, matcher=matcher)
    assert (second.new_positions, second.changed_positions) == (0, 0)  # all unchanged
    assert (second.matched, second.match_pending) == (
        3,
        0,
    )  # the backlog scored, none left
    assert store.pending_matches(limit=10) == []  # backlog cleared
    assert store.position_counts()["matched"] == 5  # all five now carry a verdict
    # the second predictor batch is exactly the drained tail (T0/T1/T2), not the scored two.
    predictor = cast(_FakePredictor, matcher._predict)
    assert {cast(str, j["title"]) for j in predictor.batches[-1]} == {
        "T0 Backend Engineer",
        "T1 Backend Engineer",
        "T2 Backend Engineer",
    }
    store.close()


# --- dedup / hardening / budget --------------------------------------------


def test_dedup_honors_the_tracking_param_denylist(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_TRACKING_PARAM_DENYLIST", "ref")
    store = StateStore(":memory:")
    summary = crawl_site(
        "s",
        _crawl(
            _job("https://example.com/a?ref=1"),
            _job("https://example.com/a?ref=2"),  # same posting, different tracking
        ),
        _ctx(),
        store,
    )
    assert summary.positions_found == 2
    assert summary.new_positions == 1  # both collapse to one canonical url
    assert len(store.open_positions("s")) == 1
    store.close()


_XXE_FEED = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    "<rss><channel><item><title>&xxe;</title></item></channel></rss>"
)


def test_crawl_hardens_untrusted_xml(monkeypatch: MonkeyPatch) -> None:
    # A crawler that parses an attacker feed with an XXE entity. The runner hardens XML
    # (§8) before running it, so the parse raises and the failure is isolated — no
    # position, no exfiltration.
    import jobcrawler.runtime.hardening as hardening

    monkeypatch.setattr(hardening, "_hardened", False)  # force a fresh defuse

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        import xml.etree.ElementTree as ET

        ET.fromstring(_XXE_FEED)  # must raise after harden_xml()
        yield _job("https://example.com/a")

    store = StateStore(":memory:")
    summary = crawl_site("s", crawl, _ctx(), store)
    assert summary.positions_found == 0  # the malicious parse was blocked
    assert summary.errors == 1  # isolated, not a fleet-aborting crash
    store.close()


def test_runaway_crawler_is_bounded_by_the_page_budget() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        for i in range(100):
            ctx.fetch(f"https://example.com/{i}")
            yield _job(f"https://example.com/{i}")

    ctx = _ctx(lambda r: httpx.Response(200, text="ok"), max_pages=3)
    store = StateStore(":memory:")
    summary = crawl_site("s", crawl, ctx, store)
    assert (
        summary.positions_found == 3
    )  # only what was yielded before the budget tripped
    assert summary.errors == 1  # PageLimitError isolated to this site
    store.close()


# -- §4.4 committed-crawl subprocess isolation -------------------------------
#
# Production crawls run the committed script in an isolated child (rlimits +
# wall-clock) so a runaway one can't wedge or OOM the daemon. These drive the real
# subprocess from source; the host still owns dedup + tracking.

_ISOLATED_SRC = """
from jobcrawler.runtime.contract import Job
SITE = "https://example.com"
def crawl(ctx):
    yield Job(url="https://example.com/a", title="Alpha Engineer", company="Acme",
              location="Remote", description="x" * 600, source="example")
    yield Job(url="https://example.com/b", title="Beta Engineer", company="Acme",
              location="Remote", description="y" * 600, source="example")
"""


def test_crawl_site_isolated_tracks_full_jobs() -> None:
    store = StateStore(":memory:")
    summary = crawl_site_isolated(
        "example", _ISOLATED_SRC, {"example.com"}, store, timeout=20.0
    )
    assert (summary.positions_found, summary.new_positions, summary.errors) == (2, 2, 0)
    tracked = {p.title for p in store.open_positions("example")}
    assert tracked == {"Alpha Engineer", "Beta Engineer"}
    # Full descriptions survive the subprocess boundary (no 600-char sample cap on a crawl)
    assert all(len(p.content_hash) == 64 for p in store.open_positions("example"))
    store.close()


def test_crawl_site_isolated_records_a_run() -> None:
    store = StateStore(":memory:")
    crawl_site_isolated("example", _ISOLATED_SRC, {"example.com"}, store, timeout=20.0)
    rec = store.last_run("example")
    assert rec is not None and rec.finished is not None
    assert (rec.items_found, rec.new_items) == (2, 2)
    store.close()


def test_crawl_site_isolated_bounds_a_hang_without_closing_the_board() -> None:
    # A committed crawler that spins forever is killed by the wall-clock bound, recorded
    # as a failed run (0 positions, 1 error) — and because the enumeration was cut short,
    # the guard skips closure so a prior crawl's positions are NOT aged out.
    store = StateStore(":memory:")
    crawl_site_isolated("example", _ISOLATED_SRC, {"example.com"}, store, timeout=20.0)
    assert len(store.open_positions("example")) == 2

    hang = "SITE='https://example.com'\ndef crawl(ctx):\n    while True:\n        pass\n    yield None\n"
    summary = crawl_site_isolated(
        "example", hang, {"example.com"}, store, timeout=1.0, close_after_misses=1
    )
    assert summary.positions_found == 0
    assert summary.errors == 1  # the timeout, folded into the run's error count
    assert summary.closed_positions == 0  # guard held: a broken crawl closes nothing
    assert len(store.open_positions("example")) == 2  # both survive
    rec = store.last_run("example")
    assert rec is not None and rec.finished is not None  # run still finalized
    store.close()


def test_probe_crawler_counts_only_complete_positions_no_track() -> None:
    store = StateStore(":memory:")

    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield _job("https://example.com/full", description="x" * 400)
        yield _job("https://example.com/stub", description="short")

    assert probe_crawler(crawl, _ctx(), min_description_chars=300) == 1
    store.close()


def test_probe_crawler_is_zero_when_nothing_complete() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield _job("https://example.com/a", description="short")

    assert probe_crawler(crawl, _ctx(), min_description_chars=300) == 0


def test_probe_crawler_drops_expired_markers() -> None:
    def crawl(ctx: CtxProtocol) -> Iterable[Job]:
        yield _job(
            "https://example.com/a", description="Role is closed now. " + "x" * 400
        )

    assert (
        probe_crawler(
            crawl,
            _ctx(),
            min_description_chars=300,
            expired_markers=("role is closed",),
        )
        == 0
    )


def test_probe_isolated_counts_only_complete_positions() -> None:
    src = """
from jobcrawler.runtime.contract import Job
SITE = "https://example.com"
def crawl(ctx):
    yield Job(url=SITE + "/full", title="F", company="Acme", location="Remote",
              description="x" * 400, source="s")
    yield Job(url=SITE + "/stub", title="T", company="Acme", location="Remote",
              description="short", source="s")
"""
    assert (
        probe_isolated(src, {"example.com"}, min_description_chars=300, timeout=20.0)
        == 1
    )


def test_probe_isolated_is_zero_on_a_hang_never_raising() -> None:
    # The §4.8 re-probe must never raise into the heal loop; a hung crawler probes to 0.
    hang = "SITE='https://example.com'\ndef crawl(ctx):\n    while True:\n        pass\n    yield None\n"
    assert (
        probe_isolated(hang, {"example.com"}, min_description_chars=300, timeout=1.0)
        == 0
    )


# --- §4.7 position fields: salary-omission clobber fix + §16 dedup key -------


def test_position_fields_omits_salary_columns_when_unstated() -> None:
    # A salary-less crawl must OMIT the salary columns (not send NULLs), so a re-crawl can't
    # null out a salary the §4.7 extractor backfilled. The §16 canonical key is always sent.
    from jobcrawler.runtime.normalize import enrich
    from jobcrawler.runtime.runner import _position_fields

    job = _job(company="Acme, Inc.")  # default description states no pay
    enriched = enrich(job)
    assert enriched.salary is None
    fields = _position_fields(job, enriched)
    for key in ("salary_min", "salary_max", "salary_currency", "salary_period"):
        assert key not in fields
    assert fields["company_canonical"] == "acme"  # suffix-stripped dedup key


def test_position_fields_includes_a_source_salary() -> None:
    # When the source DOES state pay (§4.6 enrichment carries it), the columns are present
    # and carry the parsed figures — a source salary always wins over a later LLM guess.
    from dataclasses import replace

    from jobcrawler.runtime.normalize import SalaryRange, enrich
    from jobcrawler.runtime.runner import _position_fields

    job = _job()
    enriched = replace(
        enrich(job), salary=SalaryRange(min=150000.0, max=200000.0, currency="USD", period="year")
    )
    fields = _position_fields(job, enriched)
    assert fields["salary_min"] == 150000.0 and fields["salary_max"] == 200000.0
    assert fields["salary_currency"] == "USD" and fields["salary_period"] == "year"


def test_recrawl_without_salary_keys_preserves_a_backfilled_salary() -> None:
    # End-to-end clobber guard: a salary-less upsert, then a §4.7 backfill, then an UNCHANGED
    # re-crawl (same content_hash, salary keys omitted) must leave the backfilled salary intact.
    from jobcrawler.runtime.normalize import enrich
    from jobcrawler.runtime.runner import _content_hash, _position_fields

    store = StateStore(":memory:")
    try:
        job = _job()
        enriched = enrich(job)
        chash = _content_hash(job)
        store.upsert_position(
            "example", job.url, fields=_position_fields(job, enriched), content_hash=chash
        )
        assert store.set_salary(
            "example", job.url, salary_min=120000.0, salary_max=120000.0,
            salary_currency="EUR", salary_period="year",
        )
        # a later unchanged crawl re-upserts the same salary-less fields
        store.upsert_position(
            "example", job.url, fields=_position_fields(job, enriched), content_hash=chash
        )
        rec = store.open_positions("example")[0]
        assert (rec.salary_min, rec.salary_max) == (120000.0, 120000.0)
        assert (rec.salary_currency, rec.salary_period) == ("EUR", "year")
    finally:
        store.close()
