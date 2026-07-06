"""Tests for the host-side profile matcher (§8 — the only new LLM surface).

The LM boundary is mocked throughout: the ``Matcher`` builds a ``dspy.Predict`` that a
test replaces with a fake predictor, so NO network and NO real LM are ever touched. The
suite pins the token-economy contract: the deterministic prefilter drops only definite
mismatches (and passes ambiguous buckets), the content+profile+model+version cache
scores a job at most once, misses batch, the daily cap parks overflow pending, and any
malformed LLM output fails open to pending instead of crashing the crawl loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

from jobcrawler.match import (
    PROMPT_VERSION,
    MatchJobs,
    MatchVerdict,
    Matcher,
    _content_hash,
    _verdict_to_json,
    prefilter,
)
from jobcrawler.profile import Profile, ProfilePrefs, SalaryFloor

_DAY = "2026-07-02"


def _clock() -> datetime:
    return datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


# --- fakes (duck-typed to the pinned Job/EnrichedJob/StateStore surfaces) ---


@dataclass
class FakeJob:
    title: str = "Senior Backend Engineer"
    company: str = "Acme"
    location: str = "Remote"
    description: str = "We build distributed payment systems in Rust."
    source: str = "acme"
    url: str = "https://acme.example/jobs/1"
    salary: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    employment_type: str | None = "full-time"
    department: str | None = "Engineering"


@dataclass
class FakeSalaryRange:
    min: float | None
    max: float | None
    currency: str
    period: str


@dataclass
class FakeEnriched:
    job: FakeJob
    discipline: str = "backend"
    level: str = "senior"
    workplace: str = "remote"
    salary: FakeSalaryRange | None = None


class FakeGovernor:
    def __init__(self) -> None:
        self.paces = 0

    def pace(self) -> None:
        self.paces += 1


class FakePredictor:
    """Stands in for ``dspy.Predict(MatchJobs)``: records calls, returns canned JSON."""

    def __init__(self, respond) -> None:
        self.calls = 0
        self.batches: list[list[dict]] = []
        self._respond = respond

    def __call__(self, *, profile: str, jobs: str) -> SimpleNamespace:
        self.calls += 1
        parsed = json.loads(jobs)
        self.batches.append(parsed)
        return SimpleNamespace(verdicts=self._respond(parsed))


@dataclass
class FakeStore:
    spent: int = 0
    cache: dict = field(default_factory=dict)
    set_match_calls: list = field(default_factory=list)
    pending_calls: list = field(default_factory=list)
    cache_match_calls: list = field(default_factory=list)
    bump_calls: list = field(default_factory=list)
    reserve_calls: list = field(default_factory=list)

    def cached_match(self, content_hash, profile_hash, model, prompt_version):
        return self.cache.get((content_hash, profile_hash, model, prompt_version))

    def cache_match(
        self, content_hash, profile_hash, model, prompt_version, verdict_json
    ):
        self.cache[(content_hash, profile_hash, model, prompt_version)] = verdict_json
        self.cache_match_calls.append(
            (content_hash, profile_hash, model, prompt_version, verdict_json)
        )

    def set_match(self, site, url, *, score, verdict, reasons, concerns="[]"):
        self.set_match_calls.append((site, url, score, verdict, reasons, concerns))

    def set_match_pending(self, site, url):
        self.pending_calls.append((site, url))

    def match_spent(self, day):
        return self.spent

    def bump_match_spend(self, day, n):
        self.spent += n
        self.bump_calls.append((day, n))

    def reserve_match_spend(self, day, n, *, cap):
        # Mirrors StateStore.reserve_match_spend: atomic read+increment, returns the grant.
        granted = max(0, min(n, cap - self.spent))
        self.spent += granted
        self.reserve_calls.append((day, n, granted))
        return granted


# --- builders ---------------------------------------------------------------


def _prefs(
    *,
    disciplines=(),
    levels=(),
    workplace=(),
    exclude_keywords=(),
    exclude_companies=(),
    exclude_locations=(),
    min_salary=None,
) -> ProfilePrefs:
    return ProfilePrefs(
        disciplines=frozenset(disciplines),
        levels=frozenset(levels),
        workplace=frozenset(workplace),
        locations=(),
        min_salary=min_salary,
        languages=(),
        exclude_keywords=tuple(exclude_keywords),
        exclude_companies=tuple(exclude_companies),
        exclude_locations=tuple(exclude_locations),
    )


def _profile(
    *,
    body="Senior backend/distributed-systems engineer, remote only.",
    prefs=None,
    profile_hash="profhash",
) -> Profile:
    return Profile(
        raw="raw", body=body, prefs=prefs or _prefs(), profile_hash=profile_hash
    )


def _matcher(
    store,
    *,
    model="openrouter/test/model",
    batch=10,
    daily_cap=1000,
    profile=None,
    governor=None,
):
    return Matcher(
        store,
        profile or _profile(),
        model=model,
        batch=batch,
        daily_cap=daily_cap,
        governor=governor or FakeGovernor(),
        clock=_clock,
    )


def _candidates(n, *, prefix="job"):
    out = []
    for i in range(n):
        enriched = FakeEnriched(job=FakeJob(title=f"Senior Backend Engineer {i}"))
        out.append(("acme", f"https://acme.example/{prefix}/{i}", enriched))
    return out


def _all_score(score=85, *, verdict=None, reasons=("solid fit",), concerns=()):
    """A responder that scores every job in the batch, echoing its index."""

    def respond(jobs):
        out = []
        for job in jobs:
            item = {
                "index": job["index"],
                "score": score,
                "reasons": list(reasons),
                "concerns": list(concerns),
            }
            if verdict is not None:
                item["verdict"] = verdict
            out.append(item)
        return json.dumps(out)

    return respond


# --- prefilter: every drop reason + the recall-safe passes -------------------


def test_prefilter_drops_non_engineering() -> None:
    enriched = FakeEnriched(job=FakeJob(), discipline="non_eng")
    keep, reason = prefilter(enriched, _prefs())
    assert keep is False and "non-engineering" in reason


def test_prefilter_drops_non_eng_even_with_empty_prefs() -> None:
    # Non-engineering is a definite reject regardless of an otherwise-empty profile.
    keep, _ = prefilter(FakeEnriched(job=FakeJob(), discipline="non_eng"), _prefs())
    assert keep is False


def test_prefilter_drops_discipline_not_in_preferences() -> None:
    enriched = FakeEnriched(job=FakeJob(), discipline="data")
    keep, reason = prefilter(enriched, _prefs(disciplines=("backend",)))
    assert keep is False and "discipline" in reason


def test_prefilter_passes_eng_other_regardless_of_prefs() -> None:
    # eng_other is recall-safe: too little signal to reject deterministically.
    enriched = FakeEnriched(job=FakeJob(), discipline="eng_other")
    assert prefilter(enriched, _prefs(disciplines=("backend",))) == (True, "")


def test_prefilter_passes_known_discipline_in_prefs() -> None:
    enriched = FakeEnriched(job=FakeJob(), discipline="backend")
    assert prefilter(enriched, _prefs(disciplines=("backend", "frontend")))[0] is True


def test_prefilter_no_discipline_constraint_when_empty() -> None:
    enriched = FakeEnriched(job=FakeJob(), discipline="data")
    assert prefilter(enriched, _prefs())[0] is True


def test_prefilter_drops_level_not_in_preferences() -> None:
    enriched = FakeEnriched(job=FakeJob(), level="junior")
    keep, reason = prefilter(enriched, _prefs(levels=("senior", "staff_plus")))
    assert keep is False and "level" in reason


def test_prefilter_passes_unspecified_level() -> None:
    # unspecified level is recall-safe — the LLM judges seniority from the text.
    enriched = FakeEnriched(job=FakeJob(), level="unspecified")
    assert prefilter(enriched, _prefs(levels=("senior",))) == (True, "")


def test_prefilter_drops_onsite_when_remote_only() -> None:
    enriched = FakeEnriched(job=FakeJob(), workplace="onsite")
    keep, reason = prefilter(enriched, _prefs(workplace=("remote",)))
    assert keep is False and "onsite" in reason


def test_prefilter_passes_hybrid_when_remote_only() -> None:
    enriched = FakeEnriched(job=FakeJob(), workplace="hybrid")
    assert prefilter(enriched, _prefs(workplace=("remote",))) == (True, "")


def test_prefilter_passes_onsite_when_not_strictly_remote_only() -> None:
    # A profile that also accepts hybrid lets onsite through to the LLM.
    enriched = FakeEnriched(job=FakeJob(), workplace="onsite")
    assert prefilter(enriched, _prefs(workplace=("remote", "hybrid")))[0] is True


def test_prefilter_passes_onsite_when_workplace_unconstrained() -> None:
    enriched = FakeEnriched(job=FakeJob(), workplace="onsite")
    assert prefilter(enriched, _prefs())[0] is True


def test_prefilter_drops_excluded_keyword_in_title() -> None:
    enriched = FakeEnriched(job=FakeJob(title="Backend Engineer (clearance required)"))
    keep, reason = prefilter(enriched, _prefs(exclude_keywords=("clearance required",)))
    assert keep is False and "keyword" in reason


def test_prefilter_drops_excluded_company_case_insensitive() -> None:
    enriched = FakeEnriched(job=FakeJob(company="BodyShop LLC"))
    keep, reason = prefilter(enriched, _prefs(exclude_companies=("bodyshop llc",)))
    assert keep is False and "company" in reason


def test_prefilter_drops_country_locked_remote_role() -> None:
    # A remote posting whose location is scoped to an excluded country is a definite
    # reject for a remote-only, worldwide-OK profile (PROFILE.md "What to reject").
    enriched = FakeEnriched(job=FakeJob(location="Remote, US"), workplace="remote")
    keep, reason = prefilter(enriched, _prefs(exclude_locations=("remote, us",)))
    assert keep is False and "location" in reason


def test_prefilter_country_lock_is_case_insensitive() -> None:
    enriched = FakeEnriched(
        job=FakeJob(location="United States - Remote Opportunity"), workplace="remote"
    )
    keep, _ = prefilter(enriched, _prefs(exclude_locations=("united states",)))
    assert keep is False


def test_prefilter_keeps_worldwide_remote_despite_lock_list() -> None:
    # A globally-open remote role contains no lock marker, so it passes to the LLM.
    enriched = FakeEnriched(
        job=FakeJob(location="Anywhere in the World"), workplace="remote"
    )
    prefs = _prefs(exclude_locations=("remote, us", "united states", "north america"))
    assert prefilter(enriched, prefs) == (True, "")


def test_prefilter_country_lock_only_applies_to_remote() -> None:
    # A country name on a hybrid/onsite posting is the office, not a remote lock — that
    # axis is judged elsewhere, so the location filter leaves it alone (recall-safe).
    enriched = FakeEnriched(job=FakeJob(location="Remote, US"), workplace="hybrid")
    assert prefilter(enriched, _prefs(exclude_locations=("remote, us",)))[0] is True


def test_prefilter_no_location_constraint_when_empty() -> None:
    enriched = FakeEnriched(job=FakeJob(location="Remote, US"), workplace="remote")
    assert prefilter(enriched, _prefs())[0] is True


def test_prefilter_keeps_a_clean_match() -> None:
    enriched = FakeEnriched(job=FakeJob(title="Senior Backend Engineer"))
    prefs = _prefs(disciplines=("backend",), levels=("senior",), workplace=("remote",))
    assert prefilter(enriched, prefs) == (True, "")


# --- score_new: cache, batching, cap, fail-open ------------------------------


def test_cache_hit_short_circuits_without_calling_the_llm() -> None:
    store = FakeStore()
    profile = _profile()
    enriched = FakeEnriched(job=FakeJob(title="Cached Role"))
    key = (
        _content_hash(enriched.job),
        profile.profile_hash,
        "openrouter/test/model",
        PROMPT_VERSION,
    )
    store.cache[key] = _verdict_to_json(MatchVerdict(91, "strong", ("prior",), ()))

    governor = FakeGovernor()
    matcher = _matcher(store, profile=profile, governor=governor)
    predictor = FakePredictor(_all_score())
    matcher._predict = predictor

    summary = matcher.score_new([("acme", "https://acme.example/x", enriched)])

    assert predictor.calls == 0  # the whole point: a cache hit spends nothing
    assert governor.paces == 0
    assert summary["cached"] == 1 and summary["scored"] == 0 and summary["pending"] == 0
    assert len(store.set_match_calls) == 1  # verdict still applied to the position
    assert store.set_match_calls[0][2] == 91
    assert store.bump_calls == []  # cache hits do not draw down the daily budget


def test_batching_math_23_jobs_batch_10_makes_three_calls() -> None:
    store = FakeStore()
    governor = FakeGovernor()
    matcher = _matcher(store, batch=10, daily_cap=1000, governor=governor)
    predictor = FakePredictor(_all_score(88))
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(23))

    assert predictor.calls == 3  # ceil(23/10)
    assert [len(b) for b in predictor.batches] == [10, 10, 3]
    assert governor.paces == 3  # paced once per LLM call
    assert (
        summary["scored"] == 23 and summary["pending"] == 0 and summary["cached"] == 0
    )
    # Budget is reserved atomically UP FRONT (one grant for all 23 misses), not bumped
    # after scoring — the fix that stops concurrent crawls racing the shared daily cap.
    assert store.reserve_calls == [(_DAY, 23, 23)]
    assert store.bump_calls == []  # the late-bump path is gone


def test_daily_cap_boundary_scores_up_to_cap_and_parks_the_rest() -> None:
    store = FakeStore(spent=0)
    matcher = _matcher(store, batch=10, daily_cap=5)
    predictor = FakePredictor(_all_score())
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(8))

    assert summary["scored"] == 5 and summary["pending"] == 3
    assert predictor.calls == 1  # only the 5 affordable misses go to the LLM
    assert len(store.set_match_calls) == 5
    assert len(store.pending_calls) == 3
    # 8 misses offered, cap 5 → grant 5 reserved atomically up front; 3 parked.
    assert store.reserve_calls == [(_DAY, 8, 5)]
    assert store.bump_calls == []


def test_daily_cap_already_exhausted_parks_everything() -> None:
    store = FakeStore(spent=5)
    matcher = _matcher(store, daily_cap=5)
    predictor = FakePredictor(_all_score())
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(3))

    assert predictor.calls == 0
    assert summary["scored"] == 0 and summary["pending"] == 3
    assert store.bump_calls == []


def test_daily_cap_charged_up_front_even_when_a_batch_fails() -> None:
    # The daily cap is a TOKEN budget: a batch that reaches the LLM and fails still burned
    # tokens, so its grant stays charged (reserved up front), never refunded. The old
    # read-then-late-bump path bumped only successfully-scored jobs, so a wholly-failed run
    # left the cap free — the under-count that let a shared matcher overspend on retry.
    store = FakeStore()
    matcher = _matcher(store, batch=10, daily_cap=10)
    matcher._predict = FakePredictor(lambda jobs: "not json {{{")  # every batch fails

    summary = matcher.score_new(_candidates(4))

    assert summary["scored"] == 0 and summary["pending"] == 4  # fail-open parks them
    assert store.reserve_calls == [(_DAY, 4, 4)]  # 4 sent → 4 charged before the call
    assert store.spent == 4  # charged despite 0 successfully scored (safe over-count)


def test_reserved_budget_is_cumulative_across_calls() -> None:
    # Two score_new calls sharing a store+day (as concurrent crawls share ONE matcher) draw
    # from a single cap: the second gets only what the first left. Locks the atomic reserve
    # — the old code re-read ``spent`` at the top of each call and could double-spend across
    # concurrent runs, overshooting the cap by up to the fan-out.
    store = FakeStore()
    matcher = _matcher(store, batch=10, daily_cap=5)
    matcher._predict = FakePredictor(_all_score())

    def _cands(tag: str, n: int) -> list:
        return [
            (
                "acme",
                f"https://acme.example/{tag}/{i}",
                FakeEnriched(job=FakeJob(title=f"{tag} Engineer {i}")),
            )
            for i in range(n)
        ]

    first = matcher.score_new(_cands("alpha", 3))
    second = matcher.score_new(_cands("beta", 4))  # disjoint titles → no cache sharing

    assert first["scored"] == 3  # 3 of the 5-job budget
    assert (second["scored"], second["pending"]) == (2, 2)  # only 2 budget remained
    assert store.spent == 5  # cumulative reserve never exceeds the cap
    assert store.reserve_calls == [(_DAY, 3, 3), (_DAY, 4, 2)]


def test_malformed_llm_output_parks_pending_and_does_not_crash() -> None:
    store = FakeStore()
    matcher = _matcher(store, batch=10)
    predictor = FakePredictor(lambda jobs: "this is not JSON at all {{{")
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(2))

    assert predictor.calls == 1  # it did reach the LLM, then failed to parse
    assert summary["scored"] == 0 and summary["pending"] == 2
    assert len(store.pending_calls) == 2
    assert store.cache_match_calls == []  # nothing cached from a failed batch


def test_length_mismatch_degrades_per_index_not_per_batch() -> None:
    # 2 jobs, 1 verdict: the verdict we DID pay for is applied; only the job the model
    # skipped parks pending. (Failing the whole batch would also cache-replay chatty
    # responses as permanent failures — see the salvage tests below.)
    store = FakeStore()
    matcher = _matcher(store, batch=10)
    predictor = FakePredictor(lambda jobs: json.dumps([{"index": 0, "score": 90}]))
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(2))  # 2 jobs, 1 verdict → mismatch

    assert summary["scored"] == 1 and summary["pending"] == 1


def test_missing_index_parks_only_that_job() -> None:
    store = FakeStore()
    matcher = _matcher(store, batch=10)
    # Length matches (2), but one item points at a non-existent index 5, so job 1
    # has no verdict and is parked while job 0 is applied.
    predictor = FakePredictor(
        lambda jobs: json.dumps([{"index": 0, "score": 90}, {"index": 5, "score": 70}])
    )
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(2))

    assert summary["scored"] == 1 and summary["pending"] == 1
    assert len(store.set_match_calls) == 1
    assert len(store.pending_calls) == 1


def test_scores_are_clamped_to_0_100() -> None:
    store = FakeStore()
    matcher = _matcher(store, batch=10)

    def respond(jobs):
        scores = {0: 150, 1: -20}
        return json.dumps(
            [{"index": j["index"], "score": scores[j["index"]]} for j in jobs]
        )

    matcher._predict = FakePredictor(respond)
    summary = matcher.score_new(_candidates(2))

    scores = sorted(v.score for _s, _u, v in summary["results"])
    assert scores == [0, 100]


def test_unknown_verdict_label_is_derived_from_score() -> None:
    store = FakeStore()
    matcher = _matcher(store, batch=10)

    def respond(jobs):
        rows = {0: {"score": 85, "verdict": "amazing"}, 1: {"score": 45}}
        return json.dumps([{"index": j["index"], **rows[j["index"]]} for j in jobs])

    matcher._predict = FakePredictor(respond)
    summary = matcher.score_new(_candidates(2))

    by_score = {v.score: v.verdict for _s, _u, v in summary["results"]}
    assert by_score[85] == "strong"  # garbled label → derived from score band
    assert by_score[45] == "weak"  # omitted label → derived from score band


def test_valid_verdict_label_is_preserved() -> None:
    store = FakeStore()
    matcher = _matcher(store, batch=10)
    matcher._predict = FakePredictor(_all_score(72, verdict="good"))
    summary = matcher.score_new(_candidates(1))
    assert summary["results"][0][2].verdict == "good"


def test_fresh_score_is_cached_and_a_second_run_hits_the_cache() -> None:
    store = FakeStore()
    governor = FakeGovernor()
    matcher = _matcher(store, governor=governor)
    predictor = FakePredictor(_all_score(88))
    matcher._predict = predictor
    candidates = _candidates(1)

    first = matcher.score_new(candidates)
    assert first["scored"] == 1 and len(store.cache_match_calls) == 1
    assert predictor.calls == 1

    second = matcher.score_new(candidates)  # same content + profile + model + version
    assert second["cached"] == 1 and second["scored"] == 0
    assert predictor.calls == 1  # NOT called again — scored at most once, ever
    assert governor.paces == 1


def test_reasons_and_concerns_survive_into_the_cache_roundtrip() -> None:
    store = FakeStore()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(
        _all_score(90, verdict="strong", reasons=("rust", "payments"), concerns=("tz",))
    )
    summary = matcher.score_new(_candidates(1))
    verdict = summary["results"][0][2]
    assert verdict.reasons == ("rust", "payments") and verdict.concerns == ("tz",)
    # set_match persists reasons as a JSON list on the position row.
    assert json.loads(store.set_match_calls[0][4]) == ["rust", "payments"]


def test_empty_candidates_is_a_noop() -> None:
    store = FakeStore()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())
    summary = matcher.score_new([])
    assert summary == {
        "candidates": 0,
        "cached": 0,
        "scored": 0,
        "pending": 0,
        "results": [],
    }


# --- digest: token-economy shape --------------------------------------------


def test_digest_truncates_description_and_carries_structured_fields() -> None:
    store = FakeStore()
    matcher = _matcher(store)
    predictor = FakePredictor(_all_score())
    matcher._predict = predictor
    enriched = FakeEnriched(
        job=FakeJob(
            description="x" * 2000, employment_type="contract", department="Payments"
        ),
        workplace="remote",
        salary=FakeSalaryRange(min=150000, max=200000, currency="USD", period="year"),
    )

    matcher.score_new([("acme", "https://acme.example/j", enriched)])

    digest = predictor.batches[0][0]
    assert len(digest["description"]) == 1200  # 1200-char head only (token economy)
    assert digest["workplace"] == "remote"
    assert digest["employment_type"] == "contract"
    assert digest["department"] == "Payments"
    # The parsed salary range is preferred over raw text (a number beats a phrase).
    assert digest["salary"] == {
        "min": 150000,
        "max": 200000,
        "currency": "USD",
        "period": "year",
    }


def test_digest_falls_back_to_raw_salary_text_when_unparsed() -> None:
    store = FakeStore()
    matcher = _matcher(store)
    predictor = FakePredictor(_all_score())
    matcher._predict = predictor
    enriched = FakeEnriched(job=FakeJob(salary="Competitive, DOE"), salary=None)

    matcher.score_new([("acme", "https://acme.example/j", enriched)])

    assert predictor.batches[0][0]["salary"] == "Competitive, DOE"


# --- signature + version -----------------------------------------------------


def test_prompt_version_is_pinned() -> None:
    assert PROMPT_VERSION == "match-v1"


def test_match_jobs_signature_fields() -> None:
    assert set(MatchJobs.input_fields) == {"profile", "jobs"}
    assert set(MatchJobs.output_fields) == {"verdicts"}


def test_match_jobs_rubric_is_skeptical_and_rejects_non_engineering() -> None:
    instr = MatchJobs.instructions.lower()
    assert "reject" in instr
    assert "non-engineering" in instr or "non engineering" in instr
    # the four verdict bands are enumerated in the rubric
    for band in ("strong", "good", "weak", "reject"):
        assert band in instr


def test_salary_floor_is_usable_in_prefs() -> None:
    # SalaryFloor round-trips through prefs (the matcher never blocks on it, but the
    # type must be constructible for profiles that set a floor).
    prefs = _prefs(min_salary=SalaryFloor(amount=120000, currency="USD", period="year"))
    assert prefs.min_salary is not None
    assert prefs.min_salary.amount == 120000


# --- store-side drain: rebuild parked rows into candidates (§4.7) ------------


def _seed_row(
    store,
    *,
    url,
    description="We build distributed payment systems in Rust.",
    title="Senior Backend Engineer",
    discipline="backend",
    level="senior",
    workplace="remote",
    content_hash=None,
    pending=True,
):
    """Upsert one open row the way the runner does (hash + fields from ONE job)."""
    from jobcrawler.state import StateStore  # noqa: F401  (documents the real surface)

    job = FakeJob(title=title, description=description, url=url)
    fields: dict[str, object] = {
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "discipline": discipline,
        "level": level,
        "workplace": workplace,
        "employment_type": job.employment_type,
        "department": job.department,
    }
    if description:
        fields["description"] = description
    store.upsert_position(
        "acme", url, fields=fields, content_hash=content_hash or _content_hash(job)
    )
    if pending:
        store.set_match_pending("acme", url)
    return job


def test_drain_rebuilds_a_hash_faithful_candidate_from_the_row() -> None:
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    job = _seed_row(store, url="https://acme.example/jobs/1")
    candidates, stats = drain_candidates(store, _prefs(), limit=10)

    assert stats == {"rows": 1, "unscorable": 0, "prefiltered": 0}
    ((site, url, enriched),) = candidates
    assert (site, url) == ("acme", "https://acme.example/jobs/1")
    # The rebuilt job hashes to the ORIGINAL job's content hash — the property that
    # makes drain-time verdicts land under the same cache key the runner would use.
    assert _content_hash(enriched.job) == _content_hash(job)
    assert enriched.job.description == job.description
    store.close()


def test_drain_leaves_descriptionless_rows_parked() -> None:
    # A pre-description-column row can't re-present the posting; it must WAIT for the
    # next crawl to backfill, not be silently dropped from the queue.
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    _seed_row(store, url="https://acme.example/jobs/1", description="")
    candidates, stats = drain_candidates(store, _prefs(), limit=10)

    assert candidates == [] and stats["unscorable"] == 1
    assert len(store.pending_matches(limit=10)) == 1  # still parked
    store.close()


def test_drain_rejects_a_row_whose_hash_does_not_match_its_fields() -> None:
    # Belt-and-braces: a tampered/partial row must not be scored, or the verdict would
    # be cached under a key no future lookup computes.
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    _seed_row(store, url="https://acme.example/jobs/1", content_hash="stale-hash")
    candidates, stats = drain_candidates(store, _prefs(), limit=10)

    assert candidates == [] and stats["unscorable"] == 1
    assert len(store.pending_matches(limit=10)) == 1
    store.close()


def test_drain_unparks_a_definite_prefilter_miss_without_a_verdict() -> None:
    # A profile that now definitely rejects the row must not leave it clogging the
    # FIFO forever — it leaves verdict-less, same as a fresh crawl-time prefilter miss.
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    _seed_row(store, url="https://acme.example/jobs/1", discipline="backend")
    candidates, stats = drain_candidates(
        store, _prefs(disciplines=("frontend",)), limit=10
    )

    assert candidates == [] and stats["prefiltered"] == 1
    assert store.pending_matches(limit=10) == []  # unparked...
    assert store.open_positions("acme")[0].match_score is None  # ...verdict-less
    store.close()


def test_drained_candidates_score_end_to_end_through_score_new() -> None:
    # The whole §4.7 loop against a REAL store: park → drain → score → verdict applied
    # to the row AND cached under (stored content_hash, profile, model, version).
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    job = _seed_row(store, url="https://acme.example/jobs/1")
    candidates, _stats = drain_candidates(store, _prefs(), limit=10)

    matcher = _matcher(store)
    predictor = FakePredictor(_all_score(88, verdict="strong"))
    matcher._predict = predictor
    result = matcher.score_new(candidates)

    assert result["scored"] == 1 and predictor.calls == 1
    rec = store.open_positions("acme")[0]
    assert rec.match_score == 88 and rec.match_pending is False
    assert store.cached_match(
        _content_hash(job), "profhash", "openrouter/test/model", PROMPT_VERSION
    )
    store.close()


# --- chatty-model tolerance: salvage parsing + per-index degradation ---------
#
# GLM-class models lead/trail the requested JSON with prose or pad the array. Treating
# that as fatal is worse than it looks: the malformed response is CACHED, so the same
# batch replays the same failure forever, free but making zero progress. Salvage the
# JSON value, apply verdicts by index, park only what's genuinely missing.


def _verdict_items(jobs, *, score=80):
    return [
        {
            "index": j["index"],
            "score": score,
            "verdict": "strong",
            "reasons": ["fit"],
            "concerns": [],
        }
        for j in jobs
    ]


def test_parse_salvages_json_wrapped_in_model_chatter() -> None:
    store = FakeStore()
    matcher = _matcher(store)

    def respond(jobs):
        body = json.dumps(_verdict_items(jobs))
        return f"Here are the verdicts you asked for:\n{body}\nHope this helps!"

    matcher._predict = FakePredictor(respond)
    result = matcher.score_new(_candidates(3))
    assert result["scored"] == 3 and result["pending"] == 0
    assert len(store.set_match_calls) == 3


def test_parse_still_fails_open_when_no_json_value_exists() -> None:
    # Pure prose (or a truncated, never-closing array) has nothing to salvage: the
    # whole batch parks pending, exactly as before.
    store = FakeStore()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(lambda jobs: "I cannot score these [sorry")
    result = matcher.score_new(_candidates(2))
    assert result["scored"] == 0 and result["pending"] == 2
    assert len(store.pending_calls) == 2


def test_an_extra_verdict_is_dropped_by_index_not_fatal() -> None:
    # 11 verdicts for 10 jobs used to fail the whole batch; the stray entry names no
    # job in the batch, so it is simply ignored.
    store = FakeStore()
    matcher = _matcher(store)

    def respond(jobs):
        items = _verdict_items(jobs)
        items.append({"index": 99, "score": 5, "verdict": "reject", "reasons": []})
        return json.dumps(items)

    matcher._predict = FakePredictor(respond)
    result = matcher.score_new(_candidates(3))
    assert result["scored"] == 3 and result["pending"] == 0


def test_a_missing_verdict_parks_only_that_job() -> None:
    # A short array degrades per job: batch-mates keep their (already paid-for)
    # verdicts; only the dropped index parks pending.
    store = FakeStore()
    matcher = _matcher(store)

    def respond(jobs):
        return json.dumps([i for i in _verdict_items(jobs) if i["index"] != 1])

    matcher._predict = FakePredictor(respond)
    result = matcher.score_new(_candidates(3))
    assert result["scored"] == 2 and result["pending"] == 1
    assert ("acme", "https://acme.example/job/1") in store.pending_calls


def test_match_lm_request_is_token_bounded(monkeypatch) -> None:
    # The batch LM call must carry the injected max_tokens ceiling: providers reject
    # any request whose max_tokens exceeds the remaining credit balance, so an
    # unbounded ceiling turns low credits into a total matching outage.
    from jobcrawler import match as match_mod

    captured: dict = {}
    orig_lm = match_mod.dspy.LM

    def spy(model, **kwargs):
        captured.update(kwargs)
        return orig_lm(model, **kwargs)

    monkeypatch.setattr(match_mod.dspy, "LM", spy)
    store = FakeStore()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())
    matcher.score_new(_candidates(1))
    assert captured["max_tokens"] == 4000  # the Matcher default
    assert captured["temperature"] == 0.0
