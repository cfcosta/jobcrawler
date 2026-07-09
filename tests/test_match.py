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
    EXTRACT_VERSION,
    PROMPT_VERSION,
    MatchJobs,
    MatchVerdict,
    Matcher,
    _coerce_index,
    _content_hash,
    _num_or_none,
    _parse_extraction_list,
    _to_extraction,
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
    # §17 verdict inheritance: (site, url) -> donor PositionRecord (None = no donor)
    donors: dict = field(default_factory=dict)
    donor_calls: list = field(default_factory=list)
    # §4.7 extraction surface
    extract_cache: dict = field(default_factory=dict)
    salary_set: list = field(default_factory=list)
    salary_rows: set = field(default_factory=set)
    hiring_set: list = field(default_factory=list)
    # §4.12 location surface: which (site, value) strings are the rules-parse tail,
    # which (site, url) rows already carry the loc_llm_at stamp, and what got written.
    loc_needs: dict = field(default_factory=dict)
    loc_stamped: set = field(default_factory=set)
    loc_value_set: list = field(default_factory=list)
    loc_posting_set: list = field(default_factory=list)
    loc_marked: list = field(default_factory=list)

    def location_needs_llm(self, site, value):
        return self.loc_needs.get((site, value), False)

    def location_llm_stamped(self, site, url):
        return (site, url) in self.loc_stamped

    def set_location_value_llm(self, site, value, *, places, fence, remote, model):
        self.loc_value_set.append((site, value, places, fence, remote, model))
        self.loc_needs[(site, value)] = False  # filled — mirrors first-writer-wins
        return True

    def set_position_location_llm(self, site, url, *, places, fence, remote, model):
        self.loc_posting_set.append((site, url, places, fence, remote, model))
        return True

    def mark_location_extracted(self, site, url):
        self.loc_marked.append((site, url))
        self.loc_stamped.add((site, url))

    def cached_extract(self, content_hash, model, prompt_version):
        return self.extract_cache.get((content_hash, model, prompt_version))

    def cache_extract(self, content_hash, model, prompt_version, extraction_json):
        self.extract_cache[(content_hash, model, prompt_version)] = extraction_json

    def set_salary(
        self, site, url, *, salary_min, salary_max, salary_currency, salary_period
    ):
        # Mirror StateStore.set_salary's guard: only fills a row that has no salary yet.
        if (site, url) in self.salary_rows:
            return False
        self.salary_rows.add((site, url))
        self.salary_set.append(
            (site, url, salary_min, salary_max, salary_currency, salary_period)
        )
        return True

    def set_hiring_company(self, site, url, *, name, canonical):
        self.hiring_set.append((site, url, name, canonical))

    def cached_match(self, content_hash, profile_hash, model, prompt_version):
        return self.cache.get((content_hash, profile_hash, model, prompt_version))

    def cache_match(
        self, content_hash, profile_hash, model, prompt_version, verdict_json
    ):
        self.cache[(content_hash, profile_hash, model, prompt_version)] = verdict_json
        self.cache_match_calls.append(
            (content_hash, profile_hash, model, prompt_version, verdict_json)
        )

    def set_match(
        self, site, url, *, score, verdict, reasons, concerns="[]", origin=None
    ):
        self.set_match_calls.append(
            (site, url, score, verdict, reasons, concerns, origin)
        )

    def cluster_verdict_donor(self, site, url, *, min_desc_len, tiers):
        self.donor_calls.append((site, url, min_desc_len, tiers))
        return self.donors.get((site, url))

    def set_match_pending(self, site, url):
        self.pending_calls.append((site, url))

    def match_spent(self, day):
        return self.spent

    def bump_match_spend(self, day, n):
        self.spent += n
        self.bump_calls.append((day, n))

    def reserve_match_spend(self, day, n, *, cap, reserved=0):
        # Mirrors StateStore.reserve_match_spend: atomic read+increment, returns the grant.
        # ``reserved`` holds that much of the cap back for another caller (the drain floor).
        granted = max(0, min(n, cap - reserved - self.spent))
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
    drain_floor=0,
    profile=None,
    governor=None,
    extract_model=None,
    extract_min_score=60,
    extract_cap=50,
    extract_locations=True,
):
    return Matcher(
        store,
        profile or _profile(),
        model=model,
        batch=batch,
        daily_cap=daily_cap,
        drain_floor=drain_floor,
        governor=governor or FakeGovernor(),
        clock=_clock,
        extract_model=extract_model,
        extract_min_score=extract_min_score,
        extract_cap=extract_cap,
        extract_locations=extract_locations,
    )


class FakeExtractPredictor:
    """Stands in for ``dspy.Predict(ExtractFromDescription)``: records calls, returns JSON."""

    def __init__(self, respond) -> None:
        self.calls = 0
        self.batches: list[list[dict]] = []
        self._respond = respond

    def __call__(self, *, jobs: str) -> SimpleNamespace:
        self.calls += 1
        parsed = json.loads(jobs)
        self.batches.append(parsed)
        return SimpleNamespace(extractions=self._respond(parsed))


def _extract_all(*, salary=None, hiring="", location_geo=None):
    """A responder that returns the same salary/employer/geo for every job, echoing its index."""

    def respond(jobs):
        return json.dumps(
            [
                {
                    "index": job["index"],
                    "salary": salary,
                    "hiring_company": hiring,
                    "location_geo": location_geo,
                }
                for job in jobs
            ]
        )

    return respond


def _raise_predictor(*, jobs=None):
    raise RuntimeError("extract LM exploded")


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


def test_prefilter_country_lock_sees_the_normalized_location() -> None:
    # The Working Nomads regression: the board tags US-only remote roles with the bare
    # abbreviation "USA", which contains none of the written-out profile markers — 63
    # Netflix/HubSpot US-only roles sailed past the lock and 8 reached the
    # recommendations. The §4.6 normalizer already maps USA/EUA/U.S. → "United States",
    # so the lock must test the normalized form too.
    prefs = _prefs(exclude_locations=("united states",))
    for board_location in ("USA", "Remoto, EUA", "U.S."):
        enriched = FakeEnriched(
            job=FakeJob(location=board_location), workplace="remote"
        )
        keep, reason = prefilter(enriched, prefs)
        assert keep is False, f"{board_location!r} must hit the normalized lock"
        assert "location" in reason


def test_match_prompt_hard_gates_location_eligibility() -> None:
    # The v1 rubric let the LLM score US-locked remote roles 86-88 'strong' while
    # writing the eligibility doubt into concerns. The rubric must state the gate —
    # restricted-region remote ⇒ reject; unverifiable eligibility ⇒ capped below 60 —
    # and any rubric change must ride a new PROMPT_VERSION so stale verdicts die.
    from jobcrawler.match import PROMPT_VERSION, MatchJobs

    doc = MatchJobs.__doc__ or ""
    assert "Location eligibility is a hard gate" in doc
    assert "reject" in doc
    assert PROMPT_VERSION != "match-v1"


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


def _disjoint_cands(tag: str, n: int) -> list:
    """``n`` candidates whose titles carry ``tag`` so two passes don't share the cache."""
    return [
        (
            "acme",
            f"https://acme.example/{tag}/{i}",
            FakeEnriched(job=FakeJob(title=f"{tag} Engineer {i}")),
        )
        for i in range(n)
    ]


def test_drain_floor_reserves_a_slice_of_the_cap_for_the_backlog_drain() -> None:
    # #46: fresh crawl scoring and the backlog drain share ONE matcher drawing on ONE daily
    # cap. Without a floor, a day of saturating fresh inflow reserves the whole cap and the
    # drain gets granted=0 every tick — the parked backlog never shrinks. The drain floor
    # holds a slice back: fresh may spend at most cap - floor; the drain (drain=True) may then
    # spend the reserved floor plus whatever fresh left unused.
    store = FakeStore()
    matcher = _matcher(store, batch=10, daily_cap=10, drain_floor=4)
    matcher._predict = FakePredictor(_all_score())

    # Fresh inflow tries to grab everything: capped at cap - floor = 6, the other 14 parked.
    fresh = matcher.score_new(_disjoint_cands("fresh", 20))
    assert (fresh["scored"], fresh["pending"]) == (6, 14)

    # The drain still gets the reserved floor (4), not starved to 0.
    drained = matcher.score_new(_disjoint_cands("drain", 10), drain=True)
    assert (drained["scored"], drained["pending"]) == (4, 6)
    assert store.spent == 10  # exactly the cap, no overshoot
    # Fresh reserved the 4-job floor; the drain reserved nothing and spent it.
    assert store.reserve_calls == [(_DAY, 20, 6), (_DAY, 10, 4)]


def test_drain_may_spend_fresh_leftover_above_its_floor() -> None:
    # The floor is a guarantee, not a ceiling: when fresh scoring leaves budget unused the
    # drain reserves nothing and so sweeps the remainder, not merely the floor.
    store = FakeStore()
    matcher = _matcher(store, batch=10, daily_cap=10, drain_floor=4)
    matcher._predict = FakePredictor(_all_score())

    fresh = matcher.score_new(_disjoint_cands("fresh", 2))  # light day: only 2 scored
    assert fresh["scored"] == 2

    drained = matcher.score_new(_disjoint_cands("drain", 20), drain=True)
    assert (
        drained["scored"] == 8
    )  # floor 4 + the 4 fresh left unused, not just the floor


def test_drain_floor_clamped_to_cap_never_reserves_below_zero() -> None:
    # A floor larger than the cap clamps to the cap: fresh scoring reserves the whole budget
    # (grant 0, all parked) rather than going negative, and the drain owns the full cap.
    store = FakeStore()
    matcher = _matcher(store, batch=10, daily_cap=5, drain_floor=100)
    matcher._predict = FakePredictor(_all_score())

    fresh = matcher.score_new(_disjoint_cands("fresh", 3))
    assert (fresh["scored"], fresh["pending"]) == (
        0,
        3,
    )  # floor==cap → fresh gets nothing

    drained = matcher.score_new(_disjoint_cands("drain", 8), drain=True)
    assert (drained["scored"], drained["pending"]) == (
        5,
        3,
    )  # drain spends the full cap


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


def test_stringified_index_is_applied_not_parked() -> None:
    # A model that echoes the index as a STRING ("0") must still resolve to the int the
    # batch loop looks up by — otherwise EVERY job in the batch misses, parks, and the §8
    # daily cap is re-charged on the next drain (audit #34-2.1).
    store = FakeStore()
    matcher = _matcher(store, batch=10)
    predictor = FakePredictor(
        lambda jobs: json.dumps([{"index": str(j["index"]), "score": 88} for j in jobs])
    )
    matcher._predict = predictor

    summary = matcher.score_new(_candidates(2))

    assert summary["scored"] == 2 and summary["pending"] == 0
    assert len(store.set_match_calls) == 2
    assert store.pending_calls == []


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
        "inherited": 0,
        "scored": 0,
        "pending": 0,
        "extracted": 0,
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
    # v2: location eligibility became a hard gate in the rubric. Any future MatchJobs
    # edit must bump this again — the version is a cache key, so a silent prompt change
    # would otherwise reuse verdicts scored under the old rubric.
    assert PROMPT_VERSION == "match-v2"


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
    # A pre-description-column row can't re-present the posting; it must WAIT for the next
    # crawl to backfill. pending_matches now EXCLUDES it from the drain window so it can't
    # wedge the FIFO ahead of scorable rows — but it stays parked (match_pending) so the
    # backfilling crawl re-presents it.
    from jobcrawler.match import drain_candidates
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    _seed_row(store, url="https://acme.example/jobs/1", description="")
    candidates, stats = drain_candidates(store, _prefs(), limit=10)

    # Excluded upstream, so the drain sees zero rows (nothing to classify unscorable)...
    assert candidates == []
    assert stats == {"rows": 0, "unscorable": 0, "prefiltered": 0}
    # ...yet the row is still parked for a future crawl to backfill its description.
    rec = store.get_position("acme", "https://acme.example/jobs/1")
    assert rec is not None and rec.match_pending is True
    store.close()


def test_pending_matches_with_description_only_excludes_unscorable_rows() -> None:
    # with_description_only (set by the LLM rebuild-drain) surfaces only rows the matcher
    # can rebuild from the store. A row with a NULL description (pre-column migration) OR an
    # empty-string one (what the runner writes for a textless post) is left out so it can't
    # wedge the FIFO window ahead of scorable rows — it stays parked until a crawl backfills
    # the text. The default (plain cache-drain) still returns all three.
    from jobcrawler.state import StateStore

    store = StateStore(":memory:")
    _seed_row(
        store, url="https://acme.example/has-desc", description="Ship Rust services."
    )
    _seed_row(store, url="https://acme.example/null-desc", description="")  # -> NULL
    store.upsert_position(
        "acme",
        "https://acme.example/empty-desc",
        fields={
            "title": "T",
            "company": "Acme",
            "location": "Remote",
            "description": "",
        },
        content_hash="h-empty",
    )
    store.set_match_pending("acme", "https://acme.example/empty-desc")

    kept = {
        rec.url for rec in store.pending_matches(limit=10, with_description_only=True)
    }
    assert kept == {"https://acme.example/has-desc"}
    # The unfiltered cache-drain keeps every parked row (scores by content_hash alone).
    assert len(store.pending_matches(limit=10)) == 3
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


# --- §4.7 match-time salary/employer extraction -----------------------------


def test_extraction_fills_salary_and_employer_for_matched_salaryless_jobs() -> None:
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))
    matcher._extract_predict = FakeExtractPredictor(
        _extract_all(
            salary={"min": 100000, "max": 150000, "currency": "usd", "period": "year"},
            hiring="Acme Payments Inc",
        )
    )
    summary = matcher.score_new(_candidates(2))

    assert summary["extracted"] == 2
    assert len(store.salary_set) == 2
    # currency upcased, floats coerced, period preserved.
    assert store.salary_set[0][2:] == (100000.0, 150000.0, "USD", "year")
    # employer recorded with its canonicalised (suffix-stripped) dedup key.
    assert len(store.hiring_set) == 2
    assert store.hiring_set[0][2:] == ("Acme Payments Inc", "acmepayments")
    # every extraction is cached (even a non-empty one) so a re-run doesn't re-bill.
    assert len(store.extract_cache) == 2


def test_extraction_applies_a_stringified_index() -> None:
    # Same coercion contract as scoring: a string "0" index from the extract model must
    # resolve to the int the batch loop looks up by, or the whole extract batch no-ops.
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))

    def respond(jobs):
        return json.dumps(
            [
                {
                    "index": str(j["index"]),
                    "salary": {"min": 100000, "max": 150000, "currency": "USD"},
                }
                for j in jobs
            ]
        )

    matcher._extract_predict = FakeExtractPredictor(respond)
    summary = matcher.score_new(_candidates(2))

    assert summary["extracted"] == 2
    assert len(store.salary_set) == 2


def test_extraction_skips_jobs_that_already_have_structured_salary() -> None:
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))
    ep = FakeExtractPredictor(
        _extract_all(salary={"min": 1, "max": 2, "currency": "USD"})
    )
    matcher._extract_predict = ep
    # candidates arrive already carrying a §4.6 structured salary; their location
    # parses clean ('full'), so the §4.12 arms stay quiet too — a bare 'Remote'
    # string would legitimately re-select these rows for description mining.
    cands = [
        (
            "acme",
            f"https://acme.example/job/{i}",
            FakeEnriched(
                job=FakeJob(
                    title=f"Senior Backend Engineer {i}",
                    location="Berlin, Germany",
                ),
                salary=FakeSalaryRange(
                    min=90000, max=90000, currency="USD", period="year"
                ),
            ),
        )
        for i in range(2)
    ]
    summary = matcher.score_new(cands)

    assert summary["extracted"] == 0
    assert ep.calls == 0
    assert store.salary_set == []


def test_extraction_skips_matches_below_min_score() -> None:
    store = FakeStore()
    matcher = _matcher(
        store, extract_model="openrouter/test/model", extract_min_score=60
    )
    matcher._predict = FakePredictor(_all_score(50))  # below the 60 gate
    ep = FakeExtractPredictor(
        _extract_all(salary={"min": 1, "max": 2, "currency": "USD"})
    )
    matcher._extract_predict = ep
    summary = matcher.score_new(_candidates(3))

    assert summary["extracted"] == 0
    assert ep.calls == 0


def test_extraction_is_off_without_an_extract_model() -> None:
    store = FakeStore()
    matcher = _matcher(store)  # extract_model=None (default)
    matcher._predict = FakePredictor(_all_score(95))
    ep = FakeExtractPredictor(
        _extract_all(salary={"min": 1, "max": 2, "currency": "USD"})
    )
    matcher._extract_predict = ep
    summary = matcher.score_new(_candidates(2))

    assert summary["extracted"] == 0
    assert ep.calls == 0
    assert store.salary_set == []


def test_extraction_cache_hit_reapplies_without_calling_the_lm() -> None:
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))
    # A predictor that would explode if the extractor ever reached the LM.
    matcher._extract_predict = _raise_predictor

    cands = _candidates(1)
    content_hash = _content_hash(cands[0][2].job)
    store.extract_cache[(content_hash, "openrouter/test/model", EXTRACT_VERSION)] = (
        json.dumps(
            {
                "salary_min": 120000.0,
                "salary_max": 120000.0,
                "salary_currency": "EUR",
                "salary_period": "year",
                "hiring_company": "Globex",
            }
        )
    )
    summary = matcher.score_new(cands)

    assert summary["extracted"] == 1  # re-applied from cache, LM untouched
    assert store.salary_set[0][2:] == (120000.0, 120000.0, "EUR", "year")
    assert store.hiring_set[0][2:] == ("Globex", "globex")


def test_extraction_respects_the_per_run_cap() -> None:
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model", extract_cap=1)
    matcher._predict = FakePredictor(_all_score(90))
    ep = FakeExtractPredictor(
        _extract_all(salary={"min": 80000, "max": 80000, "currency": "USD"})
    )
    matcher._extract_predict = ep
    summary = matcher.score_new(_candidates(3))

    # Only one fresh (cache-miss) extraction runs this pass; the rest wait for next run.
    assert summary["extracted"] == 1
    assert len(store.salary_set) == 1
    assert ep.calls == 1
    assert len(ep.batches[0]) == 1


def test_extraction_fails_open_when_the_lm_raises() -> None:
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))
    matcher._extract_predict = _raise_predictor  # blows up on the extract call
    summary = matcher.score_new(_candidates(2))

    # Scoring is unaffected; extraction quietly backs off (no crash, nothing written).
    assert summary["scored"] == 2
    assert summary["extracted"] == 0
    assert store.salary_set == []


def test_extraction_set_salary_guard_blocks_a_second_fill() -> None:
    # A source salary already on the row wins: set_salary refuses to overwrite it.
    store = FakeStore()
    store.salary_rows.add(("acme", "https://acme.example/job/0"))
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(90))
    matcher._extract_predict = FakeExtractPredictor(
        _extract_all(salary={"min": 5, "max": 6, "currency": "USD"}, hiring="Umbrella")
    )
    summary = matcher.score_new(_candidates(1))

    # The employer still records, but the guarded salary write is a no-op.
    assert store.salary_set == []
    assert store.hiring_set[0][2:] == ("Umbrella", "umbrella")
    # employer write still counts as an applied change.
    assert summary["extracted"] == 1


# --- §4.12 extraction location arms ------------------------------------------


def _candidate_at(url, location, *, title="Senior Backend Engineer", salary=None):
    job = FakeJob(url=url, title=title, location=location)
    return ("acme", url, FakeEnriched(job=job, salary=salary))


def test_extract_version_pins_the_v2_location_schema() -> None:
    # extract-v2 added location_geo to the prompt + persisted shape; any further
    # change to either must bump this again (the cache is keyed on it).
    from jobcrawler.match import EXTRACT_VERSION

    assert EXTRACT_VERSION == "extract-v2"


def test_extract_digest_includes_location_and_workplace() -> None:
    # The extractor must see the board's location string — the geo answer is
    # meaningless if the model only ever saw the description.
    from jobcrawler.match import _extract_digest

    digest = _extract_digest(3, FakeEnriched(job=FakeJob(location="Remote - US")))
    assert digest["location"] == "Remote - US"
    assert digest["workplace"] == "remote"


def test_ledger_tail_arm_is_score_independent_and_deduped_per_string() -> None:
    # A rules-'partial'/'fail' string earns an extraction even on a rejected row —
    # the parse is a property of the STRING, reused by every row carrying it — and
    # two rows with the same string take ONE slot per run.
    store = FakeStore()
    store.loc_needs[("acme", "Zorgville")] = True
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(10))  # hard reject
    matcher._extract_predict = FakeExtractPredictor(
        _extract_all(
            location_geo={
                "places": [{"city": "Zorgville", "country": "US"}],
                "remote": "none",
                "fence": [],
            }
        )
    )
    summary = matcher.score_new(
        [
            _candidate_at("https://acme.example/1", "Zorgville", title="Role A"),
            _candidate_at("https://acme.example/2", "Zorgville", title="Role B"),
        ]
    )
    assert matcher._extract_predict.calls == 1
    assert len(matcher._extract_predict.batches[0]) == 1
    assert [(c[0], c[1]) for c in store.loc_value_set] == [("acme", "Zorgville")]
    assert store.loc_marked == [("acme", "https://acme.example/1")]
    assert summary["extracted"] == 1


def test_marker_arm_mines_the_description_for_a_fence() -> None:
    # A bare 'Remote' string says nothing; a well-matched row without the
    # loc_llm_at stamp goes to the description miner, and the fence lands as
    # posting-local rows.
    store = FakeStore()
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(85))
    matcher._extract_predict = FakeExtractPredictor(
        _extract_all(
            salary=None,
            location_geo={"places": [], "remote": "fenced", "fence": ["US"]},
        )
    )
    url = "https://acme.example/1"
    matcher.score_new(
        [_candidate_at(url, "Remote", salary=FakeSalaryRange(1, 2, "USD", "year"))]
    )
    assert store.loc_posting_set == [
        ("acme", url, [], ["US"], "fenced", "openrouter/test/model")
    ]
    assert store.loc_marked == [("acme", url)]


def test_marker_arm_skips_stamped_and_low_scoring_rows() -> None:
    store = FakeStore()
    url = "https://acme.example/1"
    store.loc_stamped.add(("acme", url))
    matcher = _matcher(store, extract_model="openrouter/test/model")
    matcher._predict = FakePredictor(_all_score(85))
    matcher._extract_predict = FakeExtractPredictor(_extract_all())
    matcher.score_new(
        [_candidate_at(url, "Remote", salary=FakeSalaryRange(1, 2, "USD", "year"))]
    )
    assert matcher._extract_predict.calls == 0  # stamped → not re-selected

    low = _matcher(FakeStore(), extract_model="openrouter/test/model")
    low._predict = FakePredictor(_all_score(30))
    low._extract_predict = FakeExtractPredictor(_extract_all())
    low.score_new(
        [
            _candidate_at(
                "https://acme.example/2",
                "Remote",
                salary=FakeSalaryRange(1, 2, "USD", "year"),
            )
        ]
    )
    assert low._extract_predict.calls == 0  # marker arm is score-gated


def test_extract_locations_kill_switch_reverts_to_the_salary_gate() -> None:
    store = FakeStore()
    store.loc_needs[("acme", "Zorgville")] = True
    matcher = _matcher(
        store, extract_model="openrouter/test/model", extract_locations=False
    )
    matcher._predict = FakePredictor(_all_score(85))
    matcher._extract_predict = FakeExtractPredictor(
        _extract_all(
            location_geo={"places": [], "remote": "worldwide", "fence": []}
        )
    )
    # Salary present + tail string: with the switch off, nothing selects…
    matcher.score_new(
        [
            _candidate_at(
                "https://acme.example/1",
                "Zorgville",
                salary=FakeSalaryRange(1, 2, "USD", "year"),
            )
        ]
    )
    assert matcher._extract_predict.calls == 0
    # …and a salary-arm extraction never routes or stamps location data.
    matcher.score_new([_candidate_at("https://acme.example/2", "Zorgville")])
    assert matcher._extract_predict.calls == 1
    assert store.loc_value_set == []
    assert store.loc_posting_set == []
    assert store.loc_marked == []


# --- extraction parse/normalise units ---------------------------------------


def test_to_extraction_keeps_amount_with_currency_and_mirrors_single_figure() -> None:
    full = _to_extraction(
        {
            "salary": {
                "min": 100000,
                "max": 120000,
                "currency": "usd",
                "period": "year",
            },
            "hiring_company": "Foo",
        }
    )
    assert full is not None
    assert (
        full.salary_min,
        full.salary_max,
        full.salary_currency,
        full.salary_period,
    ) == (
        100000.0,
        120000.0,
        "USD",
        "year",
    )
    assert full.hiring_company == "Foo"

    single = _to_extraction(
        {"salary": {"min": 90000, "currency": "EUR", "period": "month"}}
    )
    assert single is not None
    assert single.salary_min == single.salary_max == 90000.0


def test_to_extraction_drops_ambiguous_or_missing_salary() -> None:
    # amount with no currency is ambiguous → no salary at all.
    no_cur = _to_extraction({"salary": {"min": 90000, "max": 100000, "currency": None}})
    assert no_cur is not None
    assert no_cur.salary_min is None and no_cur.salary_currency is None

    # unrecognised period is dropped, but a well-formed salary is still kept.
    odd_period = _to_extraction(
        {
            "salary": {
                "min": 90000,
                "max": 90000,
                "currency": "USD",
                "period": "fortnight",
            }
        }
    )
    assert odd_period is not None
    assert odd_period.salary_min == 90000.0 and odd_period.salary_period is None

    # a vague/equity posting returns salary: null but may still name an employer.
    vague = _to_extraction({"salary": None, "hiring_company": "Bar"})
    assert vague is not None
    assert vague.salary_min is None and vague.hiring_company == "Bar"

    # a non-mapping is unusable; an empty mapping is a valid "nothing found".
    assert _to_extraction("nope") is None
    empty = _to_extraction({})
    assert empty is not None
    assert empty.salary_min is None and empty.hiring_company == ""


def test_to_extraction_validates_location_geo_against_the_geo_vocabulary() -> None:
    # Junk is dropped, never coerced: countries must be ISO2 from the gazetteer
    # (case-folded), fence tokens must be ISO2 or a known macro key.
    ext = _to_extraction(
        {
            "location_geo": {
                "places": [
                    {"city": "Berlin", "country": "de"},
                    {"city": "Nowhere", "country": "XX"},
                ],
                "remote": "fenced",
                "fence": ["us", "EMEA", "narnia"],
            }
        }
    )
    assert ext is not None and ext.location_geo is not None
    assert ext.location_geo["places"] == [
        {"city": "Berlin", "subdivision": None, "country": "DE"},
        {"city": "Nowhere", "subdivision": None, "country": None},
    ]
    assert ext.location_geo["fence"] == ["US", "emea"]
    assert ext.location_geo["remote"] == "fenced"

    # A malformed geo answer never blocks the salary in the same entry.
    ext2 = _to_extraction(
        {"salary": {"min": 5, "max": 5, "currency": "USD"}, "location_geo": "garbage"}
    )
    assert ext2 is not None and ext2.location_geo is None
    assert ext2.salary_min == 5.0

    # An all-empty geo reads as None (nothing learned)…
    empty = _to_extraction(
        {"location_geo": {"places": [], "remote": "none", "fence": []}}
    )
    assert empty is not None and empty.location_geo is None
    # …but bare worldwide IS information.
    ww = _to_extraction(
        {"location_geo": {"places": [], "remote": "worldwide", "fence": []}}
    )
    assert ww is not None and ww.location_geo == {
        "places": [],
        "remote": "worldwide",
        "fence": [],
    }


def test_extraction_json_round_trip_preserves_location_geo() -> None:
    # A cache hit must re-apply the geo answer bit-for-bit across runs.
    from jobcrawler.match import _extraction_from_json, _extraction_to_json

    ext = _to_extraction(
        {
            "salary": {"min": 5, "max": 6, "currency": "USD", "period": "year"},
            "hiring_company": "Umbrella",
            "location_geo": {
                "places": [{"city": "Berlin", "country": "DE"}],
                "remote": "fenced",
                "fence": ["DE"],
            },
        }
    )
    assert ext is not None
    assert _extraction_from_json(_extraction_to_json(ext)) == ext


def test_num_or_none_never_invents_a_number() -> None:
    assert _num_or_none(True) is None  # bool is not a salary
    assert _num_or_none(-5) is None
    assert _num_or_none(0) is None
    assert _num_or_none(100000) == 100000.0
    assert _num_or_none("120,000") == 120000.0
    assert _num_or_none("$90000") == 90000.0
    assert _num_or_none("competitive") is None
    assert _num_or_none(None) is None


def test_parse_extraction_list_tolerates_model_chatter() -> None:
    assert _parse_extraction_list('```json\n[{"index": 0}]\n```') == [{"index": 0}]
    assert _parse_extraction_list('here: [{"index": 1}] done') == [{"index": 1}]
    assert _parse_extraction_list('{"extractions": [{"index": 2}]}') == [{"index": 2}]
    assert _parse_extraction_list('{"index": 3}') == [{"index": 3}]

    import pytest

    with pytest.raises(ValueError):
        _parse_extraction_list(None)
    with pytest.raises(ValueError):
        _parse_extraction_list("   ")


def test_coerce_index_normalizes_numeric_forms_and_rejects_the_rest() -> None:
    assert _coerce_index(3) == 3
    assert _coerce_index("3") == 3
    assert _coerce_index(3.0) == 3
    assert _coerce_index("3.0") == 3
    # bool is an int subclass but is never a valid index — must not collapse to 0/1.
    assert _coerce_index(True) is None
    assert _coerce_index(False) is None
    # non-numeric junk / missing → None (the item is skipped, not mis-keyed).
    assert _coerce_index("abc") is None
    assert _coerce_index(None) is None
    assert _coerce_index([0]) is None


# --- §17 cluster verdict inheritance -----------------------------------------
#
# Between the cache and the LLM: a cache-missing candidate whose cluster already
# holds a settled verdict copies it — zero LLM spend, never cached, reversible.


def _donor(site="greenhouse_acme", url="https://boards.greenhouse.io/acme/jobs/400123"):
    return SimpleNamespace(
        site=site,
        url=url,
        match_score=88,
        match_verdict="strong",
        match_reasons=json.dumps(["rust", "payments"]),
        match_concerns=json.dumps(["onsite days"]),
    )


def test_inherited_verdict_skips_llm_and_never_touches_cache_or_spend() -> None:
    store = FakeStore()
    cands = _candidates(1)
    store.donors[(cands[0][0], cands[0][1])] = _donor()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())

    summary = matcher.score_new(cands)

    assert summary["inherited"] == 1
    assert summary["scored"] == 0 and summary["pending"] == 0
    assert matcher._predict.calls == 0  # no LLM call
    assert store.reserve_calls == []  # inheritance draws nothing from the cap
    # Never written to the content-addressed cache: the donor's verdict describes
    # the donor's text; a poisoned entry would outlive the cluster edge.
    assert store.cache_match_calls == []
    site, url, score, verdict, reasons, _concerns, origin = store.set_match_calls[0]
    assert (site, url) == (cands[0][0], cands[0][1])
    assert score == 88 and verdict == "strong"
    assert json.loads(reasons) == ["rust", "payments"]
    assert origin == "cluster:greenhouse_acme\nhttps://boards.greenhouse.io/acme/jobs/400123"


def test_cache_hit_wins_over_donor_and_stamps_cache_origin() -> None:
    store = FakeStore()
    cands = _candidates(1)
    store.donors[(cands[0][0], cands[0][1])] = _donor()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())
    content_hash = _content_hash(cands[0][2].job)
    store.cache[(content_hash, "profhash", "openrouter/test/model", PROMPT_VERSION)] = (
        _verdict_to_json(
            MatchVerdict(score=70, verdict="good", reasons=("cached",), concerns=())
        )
    )

    summary = matcher.score_new(cands)

    assert summary["cached"] == 1 and summary["inherited"] == 0
    assert store.donor_calls == []  # cache hit short-circuits the donor lookup
    assert store.set_match_calls[0][6] == "cache"


def test_llm_scored_rows_stamp_llm_origin() -> None:
    store = FakeStore()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())

    matcher.score_new(_candidates(2))

    assert [c[6] for c in store.set_match_calls] == ["llm", "llm"]


def test_inheritance_disabled_by_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_DEDUP_MATCH", "0")
    store = FakeStore()
    cands = _candidates(1)
    store.donors[(cands[0][0], cands[0][1])] = _donor()
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())

    summary = matcher.score_new(cands)

    assert summary["inherited"] == 0 and summary["scored"] == 1
    assert store.donor_calls == []  # store never asked for a donor
    assert matcher._predict.calls == 1


def test_donor_lookup_passes_digest_bounded_min_desc_len() -> None:
    from jobcrawler.match import _DIGEST_CHARS

    store = FakeStore()
    short = _candidates(1)
    short[0][2].job.description = "tiny"
    long_desc = "x" * (_DIGEST_CHARS + 500)
    long = [
        (
            "acme",
            "https://acme.example/job/long",
            FakeEnriched(job=FakeJob(title="Staff Engineer", description=long_desc)),
        )
    ]
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())

    matcher.score_new(short + long)

    # The stub guard: a donor must carry at least as much text as this candidate
    # would feed the digest — capped at the digest window itself.
    assert store.donor_calls[0][2] == len("tiny")
    assert store.donor_calls[1][2] == _DIGEST_CHARS


def test_donor_without_score_falls_through_to_llm() -> None:
    store = FakeStore()
    cands = _candidates(1)
    donor = _donor()
    donor.match_score = None  # settled cluster-mate exists but was never scored
    store.donors[(cands[0][0], cands[0][1])] = donor
    matcher = _matcher(store)
    matcher._predict = FakePredictor(_all_score())

    summary = matcher.score_new(cands)

    assert summary["inherited"] == 0 and summary["scored"] == 1
    assert store.set_match_calls[0][6] == "llm"
