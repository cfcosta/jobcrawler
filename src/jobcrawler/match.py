"""Host-side profile matcher — the ONLY new LLM surface (§8, outside the sandbox).

The crawl loop stays LLM-free: committed crawlers are plain Python yielding raw
``Job`` facts, and the §4.6 normalizer derives discipline/level/workplace/salary
deterministically. Matching a job against the operator's ``PROFILE.md`` is the one
step that needs judgement, so it runs *here* — host-side, batched, cached — never
inside a crawler subprocess. Everything in this module is built to spend as few LLM
tokens as possible while never dropping a job on the floor. The token-economy
contract, in order of how aggressively it saves money:

  1. **Deterministic prefilter first.** :func:`prefilter` drops only *definite*
     mismatches (a non-engineering role; a discipline/level the profile excludes; an
     onsite role for a remote-only profile; an excluded keyword/company). It never
     reaches the LLM. Ambiguous buckets (``eng_other``/``unspecified``) deliberately
     PASS — recall over precision; the LLM is cheap relative to a missed dream job.

  2. **Content-addressed cache — a job is scored AT MOST ONCE, ever.** The cache key
     is ``(content_hash, profile_hash, model, PROMPT_VERSION)``. ``content_hash`` is
     the job's title/company/location/description (the §4.7 dedup content), so a
     re-crawl of an unchanged posting, a daemon restart, a §4.9 recheck, or a pending
     drain all hit the cache for free. Editing ``PROFILE.md`` changes ``profile_hash``
     and lazily invalidates every verdict — rescoring is automatic but only pays for
     jobs that are actually re-examined (new/changed/pending), never the whole backlog.

  3. **Batching.** Cache misses are scored ``batch`` at a time in ONE ``dspy.Predict``
     call each, on compact digests: the structured fields plus the first 1200 chars of
     the description, which carry most of the fit signal at a fraction of the tokens of
     a full multi-KB posting.

  4. **Daily cap + governor pacing as hard backstops.** At most ``daily_cap`` jobs are
     LLM-scored per UTC day (cache hits are free and don't count); the overflow is
     parked as ``match_pending`` and drained by later runs — never dropped. The
     injected governor paces the LLM calls so a big first crawl can't burst the budget.

  5. **Fail-open to ``match_pending``.** Any LLM or parse failure parks the affected
     jobs as pending and logs — it NEVER raises into the crawl loop. A matching outage
     degrades to "not scored yet", not "crawl crashed"; the crawl/dedup/closure path is
     wholly independent of whether the matcher is healthy.

The runner calls :meth:`Matcher.score_new` with the enriched new/changed positions of
a crawl and notifies the strong results itself (§4.7 pipeline sink). This module never
touches the network except through the mocked ``dspy`` LM boundary, and holds no
thresholds the generator could read (§8).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable, Protocol, Sequence, cast

import dspy

from jobcrawler import config
from jobcrawler.lm import build_lm
from jobcrawler.profile import Profile, ProfilePrefs
from jobcrawler.runtime.geo import parse_location
from jobcrawler.runtime.normalize import normalize_location

if (
    TYPE_CHECKING
):  # concrete import (no runtime coupling); StateStore has already landed
    from jobcrawler.state import PositionRecord, StateStore

_log = logging.getLogger("jobcrawler.match")

# Bumped on ANY change to the MatchJobs prompt/rubric or digest shape: it is one of
# the four cache keys, so bumping it invalidates every prior verdict (a new rubric can
# score the same job differently, so old verdicts must not be reused). Never reuse a
# version string for a different prompt.
PROMPT_VERSION = "match-v2"  # v2: location eligibility became a hard gate in MatchJobs

# The extraction cache version (§4.7 match-time salary/employer/location mining). Keyed
# WITHOUT a profile hash — salary, employer, and geography are properties of the posting,
# not of the profile — so one extraction serves every profile. Bump on ANY change to the
# ExtractFromDescription prompt or the persisted extraction shape: it invalidates every
# prior extraction (the old backlog re-mines at <=extract_cap per run as rows resurface).
EXTRACT_VERSION = "extract-v3"  # v2: location_geo (§4.12); v3: head+tail digest window

# The verdict bands. Numeric score is the source of truth; the label is derived from
# it when the LLM omits or garbles it (see _verdict_label). match_min_score (the
# notify threshold, host-side in config) is applied by the RUNNER, not here — the
# matcher is threshold-agnostic so §8 keeps the decision boundary off this surface.
_VERDICTS: frozenset[str] = frozenset({"strong", "good", "weak", "reject"})

# Discipline buckets that are recall-safe: never prefilter-dropped for "not in prefs"
# — they carry too little signal to reject deterministically, so the LLM decides.
_DISCIPLINE_PASS: frozenset[str] = frozenset({"eng_other", "unspecified"})

# How much description the LLM digest carries (chars). Shared by ``_digest`` and the
# §17 inheritance stub guard: a donor's verdict was formed from at most this window of
# ITS text, so a candidate may only inherit from a donor whose description is at least
# as informative as the candidate's own would be — capped here, where the LLM stops
# reading anyway.
_DIGEST_CHARS = 1200


# The normalizer's per-job output the matcher consumes (§4.6), modeled STRUCTURALLY
# rather than imported from jobcrawler.runtime.normalize: that module lands in parallel,
# so a structural seam keeps this file type-checkable and lets its tests use lightweight
# fakes now, while the real EnrichedJob/Job/SalaryRange satisfy these by shape at
# integration. (StateStore, which already exists, stays a concrete import above — a real
# type gives stronger checking than a Protocol, so prefer it whenever the type has landed.)
class _JobLike(Protocol):
    """The raw-``Job`` fields the matcher reads — a structural subset of the §4 contract.

    Members are read-only ``@property`` declarations on purpose: the matcher never
    mutates a job, and read-only protocol members are COVARIANT, so the concrete ``Job``
    and the test fakes satisfy this by shape. (A plain ``title: str`` member would be
    mutable and therefore invariant, rejecting any subtype — see the salary range below.)
    """

    @property
    def title(self) -> str: ...
    @property
    def company(self) -> str: ...
    @property
    def location(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def employment_type(self) -> str | None: ...
    @property
    def department(self) -> str | None: ...
    @property
    def salary(self) -> str | None: ...


class _SalaryRange(Protocol):
    """The §4.6 parsed salary range the digest prefers over the raw ``salary`` text."""

    @property
    def min(self) -> float | None: ...
    @property
    def max(self) -> float | None: ...
    @property
    def currency(self) -> str: ...
    @property
    def period(self) -> str: ...


class _Enriched(Protocol):
    """One normalized job: the raw fields plus the §4.6 derived classification."""

    @property
    def job(self) -> _JobLike: ...
    @property
    def discipline(self) -> str: ...
    @property
    def level(self) -> str: ...
    @property
    def workplace(self) -> str: ...
    @property
    def salary(self) -> _SalaryRange | None: ...


@dataclass(frozen=True, slots=True)
class MatchVerdict:
    """One job's scored fit against the profile.

    ``score`` is 0-100 (clamped on parse); ``verdict`` is a coarse band derived from
    or validated against the score; ``reasons``/``concerns`` are the LLM's short
    justifications (surfaced to the operator, never fed back to the generator, §8).
    """

    score: int
    verdict: str
    reasons: tuple[str, ...]
    concerns: tuple[str, ...]


class MatchJobs(dspy.Signature):
    """Score how well each job fits the candidate profile. Be skeptical and specific.

    You are a hiring-signal filter for one engineer. Judge each job ONLY against the
    profile, on: engineering discipline & domain fit, required skills/stack, seniority,
    workplace (remote/hybrid/onsite) and location/timezone feasibility, and salary if
    stated. Reject non-engineering roles outright. When the profile is silent on an
    axis, treat it as no constraint rather than inventing one.

    Score each job 0-100 and assign a verdict band:
      * strong (80-100): a clear, well-matched fit worth applying to now.
      * good   (60-79):  a plausible fit with minor gaps.
      * weak   (40-59):  a stretch; real mismatches on skills, seniority, or setup.
      * reject (0-39):    not a fit (wrong discipline, seniority, onsite vs remote-only,
                          excluded domain, or non-engineering).

    Location eligibility is a hard gate, not a tiebreaker. A remote role whose stated
    location or hiring scope is restricted to a country/region the profile cannot work
    from (e.g. "USA"/"United States" remote for a Brazil-based profile) is a reject
    (0-39) no matter how strong the technical fit. When the posting leaves remote
    eligibility for the profile's location unverifiable, cap the score below 60 (weak
    at best) and name the doubt in concerns — never award strong/good on fit alone
    while eligibility is unknown.

    Be conservative: a vague or under-specified posting is not automatically a strong
    fit. Do not reward keyword overlap alone. Keep reasons and concerns to a few short
    phrases each, grounded in specifics from the job and profile.

    Output ``verdicts`` as a JSON array with one object per input job, echoing that
    job's integer ``index``: {"index": int, "score": int, "verdict": str,
    "reasons": [str, ...], "concerns": [str, ...]}. Return exactly one object per input
    job, in any order; do not add commentary outside the JSON.
    """

    profile: str = dspy.InputField(
        desc="the candidate profile: skills, domains, seniority, and what to reject"
    )
    jobs: str = dspy.InputField(
        desc="JSON array of jobs, each with an integer 'index' and compact fields"
    )
    verdicts: str = dspy.OutputField(
        desc="JSON array of {index, score, verdict, reasons, concerns}, one per job"
    )


class ExtractFromDescription(dspy.Signature):
    """Extract compensation, the hiring company, and stated geography from job postings.
    COPY, do not guess.

    You get each job's integer ``index`` plus its title, the board-supplied ``company`` (may
    be blank or a placeholder like "Confidential"), its ``location`` string and ``workplace``
    as posted, and the description text. Return exactly one object per input job:

      * ``salary`` — ONLY if the description literally states the pay for THIS role. Copy the
        figures as written into ``{min, max, currency, period}``: ``currency`` an ISO-ish code
        ("USD", "EUR", "BRL"), ``period`` one of "year" | "month" | "hour". A single figure
        sets ``min`` == ``max``. Return ``salary: null`` when pay is absent, vague
        ("competitive", "DOE"), equity-only, or when NO currency is stated (an amount with no
        currency is ambiguous — do not assume one). NEVER infer a range from seniority,
        location, or market rates: no explicitly stated number means ``null``.
      * ``hiring_company`` — the real employer the description names, but ONLY when the board
        ``company`` is blank or a placeholder AND the text clearly identifies it. Otherwise
        return "". Never guess from the domain, the title, or invent a name.
      * ``location_geo`` — the geography the POSTING states, read from its ``location``
        string and description: ``places`` are offices/onsite cities it names, each
        ``{"city": str|null, "subdivision": str|null, "country": "<ISO2>"|null}`` with
        ``country`` an ISO 3166-1 alpha-2 code; ``remote`` is "worldwide" when it says
        work-from-anywhere, "fenced" when remote work is limited to named countries/regions,
        else "none"; ``fence`` lists those limits as ISO2 codes or region words ("US", "DE",
        "EMEA", "LATAM"). Return ``location_geo: null`` when neither the location string nor
        the text states geography. NEVER infer a country from the company's HQ, the salary
        currency, or the posting's language — only what is written.

    Output ``extractions`` as a JSON array, one object per input job, echoing its integer
    ``index``: {"index": int, "salary": {"min": num|null, "max": num|null,
    "currency": str|null, "period": str|null} | null, "hiring_company": str,
    "location_geo": {"places": [...], "remote": "none"|"fenced"|"worldwide",
    "fence": [str, ...]} | null}. Return exactly one object per input job, in any order; add
    no commentary outside the JSON.
    """

    jobs: str = dspy.InputField(
        desc=(
            "JSON array of jobs, each with an integer 'index', title, company, "
            "location, workplace, description"
        )
    )
    extractions: str = dspy.OutputField(
        desc=(
            "JSON array of {index, salary|null, hiring_company, location_geo|null}, "
            "one per input job"
        )
    )


def prefilter(enriched: _Enriched, prefs: ProfilePrefs) -> tuple[bool, str]:
    """Deterministically drop a job iff it is a DEFINITE mismatch (recall over precision).

    Returns ``(keep, reason)``: ``(True, "")`` to send the job on to the LLM, or
    ``(False, <why>)`` to drop it before spending a token. Only unambiguous rejections
    fire here (§8 token economy) — anything the profile is silent about, and the
    recall-safe ``eng_other``/``unspecified`` buckets, PASS so the LLM can judge. This
    is pure and side-effect-free; the runner calls it between §4.6 enrich and
    :meth:`Matcher.score_new`.
    """
    job = enriched.job
    discipline = enriched.discipline

    # Non-engineering is a definite reject regardless of prefs — the whole surface is
    # an engineering-job filter; a marketing/sales role is never in scope.
    if discipline == "non_eng":
        return (False, "non-engineering role")
    # A specific, known discipline the profile lists disciplines but doesn't include.
    if (
        discipline not in _DISCIPLINE_PASS
        and prefs.disciplines
        and discipline not in prefs.disciplines
    ):
        return (False, f"discipline {discipline!r} not in preferences")
    # A known level outside the preferred set ('unspecified' is recall-safe and passes).
    level = enriched.level
    if level != "unspecified" and prefs.levels and level not in prefs.levels:
        return (False, f"level {level!r} not in preferences")
    # Onsite only drops for a strictly remote-only profile; a profile that also accepts
    # hybrid (or is silent) lets onsite through to the LLM (a posting's 'onsite' may
    # still allow negotiation), and hybrid is always recall-safe here.
    if enriched.workplace == "onsite" and prefs.workplace == frozenset({"remote"}):
        return (False, "onsite role, profile is remote-only")

    title_lower = (job.title or "").lower()
    for keyword in prefs.exclude_keywords:
        if keyword and keyword.lower() in title_lower:
            return (False, f"excluded keyword {keyword!r} in title")
    if prefs.exclude_companies:
        company_lower = (job.company or "").lower()
        if company_lower in {c.lower() for c in prefs.exclude_companies}:
            return (False, f"excluded company {job.company!r}")

    # Country/citizenship lock: a REMOTE posting whose stated location is scoped to a
    # region the candidate can't legally work in is a definite reject for a remote-only
    # profile (PROFILE.md "What to reject"). ``exclude_locations`` holds the lock markers
    # ("remote, us", "united states", "remote (canada)", …), matched against the
    # posting's location only — a worldwide marker ("anywhere in the world", "remote")
    # never contains one, so a globally-open role passes. The markers are tested against
    # BOTH the raw location and its §4.6-normalized form: boards abbreviate ("USA",
    # "EUA", "U.S.") while the profile writes markers out ("united states"), and the
    # synonym map already bridges the two — without it, every Working Nomads
    # "USA"-tagged remote role sailed past the lock and burned a match token. Gated to
    # ``remote`` because on an onsite/hybrid posting a country name is the office
    # address, not a remote-eligibility lock, and that axis is judged above / by the LLM.
    if prefs.exclude_locations and enriched.workplace == "remote":
        raw_location = (job.location or "").lower()
        normalized_location = normalize_location(job.location or "").lower()
        for marker in prefs.exclude_locations:
            marker_lower = marker.lower()
            if marker_lower and (
                marker_lower in raw_location or marker_lower in normalized_location
            ):
                return (False, f"remote role locked to excluded location ({marker!r})")

    return (True, "")


class _Governor(Protocol):
    """The rate-pacing seam :class:`Matcher` calls before each LLM batch (§8 backstop).

    Structural on purpose (like §4.8's :class:`~jobcrawler.schedule.Healer`): the daemon
    injects its shared rate governor and a test injects a counting fake, with no import
    coupling either way. Only ``pace()`` is used here — a call that spends this batch's
    slice of the match-rate budget (blocking if it is momentarily exhausted) before the
    request goes out, so a big first crawl can't burst the daily cap in a single spike.
    """

    def pace(self) -> None: ...


class Matcher:
    """Batched, cached, capped, fail-open LLM scoring of enriched jobs (§8 host-side).

    One instance is built per daemon and reused across crawls. ``model``/``batch``/
    ``daily_cap`` are injected (not read from config) so the caller owns the budget and
    tests stay hermetic; ``governor`` paces the LLM calls (``.pace()``) and ``clock``
    supplies the UTC day for the daily cap. The ``dspy.Predict(MatchJobs)`` predictor
    is held as an attribute so a test can replace it with a fake — the LM is bound per
    call via ``dspy.context`` (temperature 0 for determinism), mirroring how the
    generator constructs its LMs.
    """

    def __init__(
        self,
        store: "StateStore",
        profile: Profile,
        *,
        model: str,
        batch: int,
        daily_cap: int,
        governor: _Governor,
        clock: Callable[[], datetime],
        max_tokens: int = 4000,
        drain_floor: int = 0,
        extract_model: str | None = None,
        extract_min_score: int = 60,
        extract_cap: int = 50,
        extract_locations: bool = True,
        concurrency: int = 1,
    ) -> None:
        self._store = store
        self._profile = profile
        self._model = model
        self._batch = max(1, batch)
        self._daily_cap = max(0, daily_cap)
        # §4.7 drain floor: how much of the daily cap fresh crawl scoring holds back so the
        # backlog drain always has a slice. Clamped to the cap (reserving more than the cap
        # would zero fresh scoring; the drain would then own the whole budget).
        self._drain_floor = max(0, min(drain_floor, self._daily_cap))
        self._governor = governor
        self._clock = clock
        # §4.7 match-time salary/employer extraction. OFF unless a (cheap) extract model is
        # given; then, after scoring, jobs that matched at/above ``extract_min_score`` but
        # carry no structured salary have their description mined — bounded to ``extract_cap``
        # fresh (cache-miss) extractions per run so a first pass can't spike token spend.
        self._extract_model = extract_model
        self._extract_min_score = extract_min_score
        self._extract_cap = max(0, extract_cap)
        # §4.12 location arms of the extract gate (kill-switch, default on). False
        # reverts the gate to salary-only and stops all location routing/stamping.
        self._extract_locations = extract_locations
        # Concurrent scoring batches in flight (§4.7). The governor paces every
        # batch START regardless (its lock serializes the waits), so concurrency
        # overlaps model LATENCY without raising the request rate; the store is
        # lock-serialized and the spend grant is charged atomically up front, so
        # workers share them safely. 1 = the classic sequential loop.
        self._concurrency = max(1, concurrency)
        # A verdict is ~200 tokens; even a full batch fits comfortably in 4k. Bounding
        # the request matters beyond cost: providers reject a call whose max_tokens
        # exceeds the remaining credit balance, so an unbounded (provider-default)
        # ceiling turns a low-credit account into a hard outage for matching.
        self._max_tokens = max(256, max_tokens)
        # Constructed offline (no LM bound yet); the LM is set per batch under
        # dspy.context. Tests overwrite this attribute with a fake predictor.
        self._predict = dspy.Predict(MatchJobs)
        # The separate cheap extractor predictor (§4.7). Also LM-less until per-batch context;
        # a test injecting extraction overwrites it with a fake, like ``_predict``.
        self._extract_predict = dspy.Predict(ExtractFromDescription)

    def score_new(
        self,
        candidates: Sequence[tuple[str, str, _Enriched]],
        *,
        drain: bool = False,
    ) -> dict[str, object]:
        """Score prefiltered new/changed jobs, returning an operational summary.

        ``candidates`` is ``(site, url, enriched)`` triples the runner already ran
        through :func:`prefilter`. The flow is: cache lookup (free hits applied
        immediately) -> daily-cap split (overflow parked pending) -> batched LLM scoring
        of the remainder -> cache + persist each verdict. Never raises: a batch that
        fails the LLM or parse is parked pending (fail-open). The returned dict carries
        counts plus a ``results`` list of ``(site, url, MatchVerdict)`` applied this run
        (cache hits and fresh scores), which the runner filters by ``match_min_score``
        to notify strong matches.

        ``drain=True`` marks the §4.7 backlog-drain / profile-edit rematch path: it may
        spend the full daily cap (the reserved drain floor plus whatever fresh scoring left
        unused). Fresh crawl scoring leaves it ``False`` and so holds ``drain_floor`` of the
        cap back, guaranteeing the drain a slice even on a day of saturating new inflow.
        """
        day = self._clock().date().isoformat()
        results: list[tuple[str, str, MatchVerdict]] = []
        cached_n = 0
        inherited_n = 0
        pending_n = 0

        # §17 verdict inheritance sits between the cache and the LLM: a cache-missing
        # candidate whose cluster already holds a settled verdict (another board's copy
        # of the same real-world job) copies it — deterministic, zero spend, and NEVER
        # written to the match cache (the cache is content-addressed and the donor's
        # verdict describes *different* text; a poisoned entry would outlive the cluster
        # edge that justified it). Reversal is wholesale: ``requeue_inherited`` re-parks
        # every 'cluster:%' row for real scoring.
        inherit = config.dedup_match_enabled()
        tiers = config.dedup_tiers() if inherit else frozenset()

        # (a) cache lookup — a hit is free and applied at once; a miss tries a §17
        # donor next, and only then queues for LLM.
        misses: list[tuple[str, str, _Enriched, str]] = []
        enriched_by_key: dict[tuple[str, str], _Enriched] = {}
        for site, url, enriched in candidates:
            enriched_by_key[(site, url)] = enriched
            content_hash = _content_hash(enriched.job)
            cached = self._store.cached_match(
                content_hash, self._profile.profile_hash, self._model, PROMPT_VERSION
            )
            verdict = _verdict_from_json(cached) if cached is not None else None
            if verdict is not None:
                self._apply(site, url, verdict, origin="cache")
                results.append((site, url, verdict))
                cached_n += 1
                continue
            donor = (
                self._store.cluster_verdict_donor(
                    site,
                    url,
                    # Stub guard: the donor must carry at least as much text as this
                    # candidate would feed the digest — a thin aggregator row never
                    # overrides fuller text (see _DIGEST_CHARS).
                    min_desc_len=min(
                        _DIGEST_CHARS, len(getattr(enriched.job, "description", "") or "")
                    ),
                    tiers=tiers,
                )
                if inherit
                else None
            )
            if donor is not None and donor.match_score is not None:
                verdict = _inherited_verdict(donor)
                self._apply(
                    site, url, verdict, origin=f"cluster:{donor.site}\n{donor.url}"
                )
                results.append((site, url, verdict))
                inherited_n += 1
            else:
                misses.append((site, url, enriched, content_hash))

        # (c) daily cap — cache hits are free; only misses draw down the budget. Reserve
        # the grant ATOMICALLY (read + increment under one store lock) so a shared matcher
        # fanned out across concurrent ``asyncio.to_thread`` crawls can't each read a stale
        # spend and collectively overshoot the cap (the TOCTOU the old match_spent-then-late
        # -bump pair allowed). The grant is charged UP FRONT — a failed batch still burned
        # tokens — and the overflow is parked pending (never dropped) for a later run to drain.
        # Fresh scoring reserves the drain floor (holds it back for the backlog drain); the
        # drain path reserves nothing, so it may spend that floor plus fresh's leftover.
        reserved = 0 if drain else self._drain_floor
        granted = (
            self._store.reserve_match_spend(
                day, len(misses), cap=self._daily_cap, reserved=reserved
            )
            if misses
            else 0
        )
        to_score = misses[:granted]
        for site, url, _enriched, _hash in misses[granted:]:
            self._store.set_match_pending(site, url)
            pending_n += 1

        # (b) batched LLM scoring of the reserved misses — overlapped when
        # ``concurrency`` > 1. Each worker paces its own START through the shared
        # governor (thread-safe), so N workers overlap model latency while call
        # starts stay rate-spaced; per-batch failure semantics are unchanged.
        scored_n = 0
        batches = list(_chunks(to_score, self._batch))

        def _run(batch: Sequence[tuple[str, str, _Enriched, str]]) -> list[
            tuple[str, str, MatchVerdict]
        ]:
            self._governor.pace()  # (d) pace the LLM calls
            return self._score_batch(batch)

        outcomes: list[
            tuple[
                Sequence[tuple[str, str, _Enriched, str]],
                list[tuple[str, str, MatchVerdict]] | None,
                Exception | None,
            ]
        ] = []
        if self._concurrency > 1 and len(batches) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
                futures = [(batch, pool.submit(_run, batch)) for batch in batches]
                for batch, future in futures:
                    try:
                        outcomes.append((batch, future.result(), None))
                    except Exception as exc:  # noqa: BLE001 — per-batch fail-open
                        outcomes.append((batch, None, exc))
        else:
            for batch in batches:
                try:
                    outcomes.append((batch, _run(batch), None))
                except Exception as exc:  # noqa: BLE001 — per-batch fail-open
                    outcomes.append((batch, None, exc))

        for batch, applied, exc in outcomes:
            if applied is None:  # (e) fail-open — a bad batch is parked, not fatal
                _log.warning(
                    "match batch of %d job(s) failed, parking pending: %s: %s",
                    len(batch),
                    type(exc).__name__ if exc else "Exception",
                    str(exc)[:200],
                )
                for site, url, _enriched, _hash in batch:
                    self._store.set_match_pending(site, url)
                    pending_n += 1
                continue
            results.extend(applied)
            scored_n += len(applied)
            # Any job the LLM omitted (missing index) was parked inside _score_batch.
            pending_n += len(batch) - len(applied)

        # No late bump: reserve_match_spend already charged the grant (== len(to_score))
        # atomically up front, so concurrent crawls sharing this matcher can't race the cap.

        # (f) match-time salary/employer/location backfill (§4.7). Three arms select a
        # row: (1) salary — matched at/above extract_min_score with no structured pay;
        # (2) ledger tail — its location string rules-parsed 'partial'/'fail'
        # (SCORE-INDEPENDENT: the parse is a property of the string, reused by every
        # row that ever carries it, and deduped to one slot per distinct string per
        # run); (3) marker — a well-matched row whose string is a bare workplace
        # marker ('Remote') and whose description hasn't been mined yet
        # (loc_llm_at IS NULL). Runs over cache hits AND fresh scores (a cached
        # extraction re-applies for free), is bounded per run, and is a no-op unless
        # an extract model is configured. Best-effort: never affects the scoring
        # result the runner notifies on.
        extracted_n = 0
        if self._extract_model is not None:
            to_extract: list[tuple[str, str, _Enriched]] = []
            tail_seen: set[tuple[str, str]] = set()
            for site, url, verdict in results:
                enriched = enriched_by_key[(site, url)]
                wants = (
                    verdict.score >= self._extract_min_score
                    and enriched.salary is None
                )
                if not wants and self._extract_locations:
                    loc = (getattr(enriched.job, "location", "") or "").strip()
                    if loc and self._store.location_needs_llm(site, loc):
                        key = (site, loc)
                        if key not in tail_seen:
                            tail_seen.add(key)
                            wants = True
                    elif (
                        loc
                        and verdict.score >= self._extract_min_score
                        and parse_location(loc).quality == "marker"
                        and not self._store.location_llm_stamped(site, url)
                    ):
                        wants = True
                if wants:
                    to_extract.append((site, url, enriched))
            extracted_n = self._extract_missing(to_extract)

        return {
            "candidates": len(candidates),
            "cached": cached_n,
            "inherited": inherited_n,
            "scored": scored_n,
            "pending": pending_n,
            "extracted": extracted_n,
            "results": results,
        }

    # -- internals -----------------------------------------------------------

    def _score_batch(
        self, batch: Sequence[tuple[str, str, _Enriched, str]]
    ) -> list[tuple[str, str, MatchVerdict]]:
        """LLM-score one batch; persist + cache each verdict; return applied results.

        Raises on an LLM/parse failure so the caller parks the whole batch pending
        (fail-open). Anything short of that degrades PER JOB: a verdict-count mismatch,
        an extra/unknown index, or an individual garbled entry parks just the affected
        jobs and applies the rest by ``index`` — one bad row (or one chatty model
        padding the array) must not waste the tokens already spent on its batch-mates.
        """
        payload = [_digest(index, e) for index, (_s, _u, e, _h) in enumerate(batch)]
        jobs_json = json.dumps(payload, ensure_ascii=False)
        # build_lm keeps the temperature=0 / bounded-max_tokens shape for an OpenRouter
        # slug, but routes a chatgpt/* slug to the Codex Responses backend (dropping both,
        # which that backend ignores and dspy's gpt-5 guard would reject) — §9.
        lm = build_lm(self._model, temperature=0.0, max_tokens=self._max_tokens)
        with dspy.context(lm=lm):
            prediction = self._predict(profile=self._profile.body, jobs=jobs_json)

        items = _parse_verdict_list(getattr(prediction, "verdicts", None))
        if len(items) != len(batch):
            _log.warning(
                "verdict count mismatch (%d for %d job(s)); applying by index",
                len(items),
                len(batch),
            )
        by_index: dict[int, object] = {}
        for item in items:
            if isinstance(item, dict):
                idx = _coerce_index(item.get("index"))
                if idx is not None:
                    by_index[idx] = item

        applied: list[tuple[str, str, MatchVerdict]] = []
        for index, (site, url, _enriched, content_hash) in enumerate(batch):
            verdict = _to_verdict(by_index.get(index))
            if verdict is None:
                self._store.set_match_pending(site, url)
                continue
            self._apply(site, url, verdict, origin="llm")
            self._store.cache_match(
                content_hash,
                self._profile.profile_hash,
                self._model,
                PROMPT_VERSION,
                _verdict_to_json(verdict),
            )
            applied.append((site, url, verdict))
        return applied

    def _apply(
        self, site: str, url: str, verdict: MatchVerdict, *, origin: str
    ) -> None:
        """Persist a verdict to the position row (clears its pending flag).

        Both ``reasons`` and ``concerns`` are stored so the durable row carries the whole
        verdict — the match trainset (§8) is projected from these rows, not the memoization
        cache, which is keyed by hashes and may be absent. ``origin`` records how the
        verdict got here (``'llm'`` | ``'cache'`` | ``'cluster:<site>\\n<url>'``) so §17
        inheritances stay reversible (``requeue_inherited``) and the trainset can exclude
        them (an inherited verdict describes the donor's text, not this row's).
        """
        self._store.set_match(
            site,
            url,
            score=verdict.score,
            verdict=verdict.verdict,
            reasons=json.dumps(list(verdict.reasons)),
            concerns=json.dumps(list(verdict.concerns)),
            origin=origin,
        )

    # -- match-time extraction (§4.7) ---------------------------------------

    def _extract_missing(self, candidates: Sequence[tuple[str, str, _Enriched]]) -> int:
        """Backfill salary/employer for salary-less matched jobs; return rows updated.

        Cache lookup first — a hit re-applies for free (keeping salary and the §16 employer
        key populated on every run, even after a crawl upsert reverts the key). Cache misses
        are extracted on the cheap LM in batches, bounded to ``extract_cap`` per run so a
        first pass over a big backlog can't spike spend; the overflow RE-PARKS
        (``set_match_pending``) so the drain re-presents those rows next tick — the verdict
        re-applies from cache for free and extraction takes a fresh slot. Never raises: a
        failed extract batch leaves those rows untouched (fail-open, like scoring)."""
        applied = 0
        misses: list[tuple[str, str, _Enriched, str]] = []
        model = cast(str, self._extract_model)
        for site, url, enriched in candidates:
            content_hash = _content_hash(enriched.job)
            cached = self._store.cached_extract(content_hash, model, EXTRACT_VERSION)
            ext = _extraction_from_json(cached) if cached is not None else None
            if ext is not None:
                applied += int(
                    self._apply_extraction(
                        site,
                        url,
                        ext,
                        location=getattr(enriched.job, "location", None),
                    )
                )
            else:
                misses.append((site, url, enriched, content_hash))
        for batch in _chunks(misses[: self._extract_cap], self._batch):
            self._governor.pace()
            try:
                applied += self._extract_batch(batch)
            except (
                Exception
            ) as exc:  # fail-open — a bad extract batch is skipped, not fatal
                _log.warning(
                    "extract batch of %d job(s) failed, skipping: %s: %s",
                    len(batch),
                    type(exc).__name__,
                    str(exc)[:200],
                )
        for site, url, _enriched, _hash in misses[self._extract_cap :]:
            # Cap overflow: scored rows never re-present on their own, so park
            # them for the drain — cache replays the verdict, extraction fresh.
            self._store.set_match_pending(site, url)
        return applied

    def _extract_batch(self, batch: Sequence[tuple[str, str, _Enriched, str]]) -> int:
        """LLM-extract one batch; cache + apply each entry; return rows updated.

        Raises on an LLM/parse failure so the caller skips the batch (fail-open). Every
        parsed entry is cached — including an empty "nothing found" one, so a genuinely
        salary-less posting is never re-billed."""
        payload = [_extract_digest(i, e) for i, (_s, _u, e, _h) in enumerate(batch)]
        jobs_json = json.dumps(payload, ensure_ascii=False)
        model = cast(str, self._extract_model)
        lm = build_lm(model, temperature=0.0, max_tokens=self._max_tokens)
        with dspy.context(lm=lm):
            prediction = self._extract_predict(jobs=jobs_json)

        items = _parse_extraction_list(getattr(prediction, "extractions", None))
        by_index: dict[int, object] = {}
        for item in items:
            if isinstance(item, dict):
                idx = _coerce_index(item.get("index"))
                if idx is not None:
                    by_index[idx] = item

        applied = 0
        for index, (site, url, enriched, content_hash) in enumerate(batch):
            ext = _to_extraction(by_index.get(index))
            if ext is None:
                continue
            self._store.cache_extract(
                content_hash, model, EXTRACT_VERSION, _extraction_to_json(ext)
            )
            applied += int(
                self._apply_extraction(
                    site, url, ext, location=getattr(enriched.job, "location", None)
                )
            )
        return applied

    def _apply_extraction(
        self, site: str, url: str, ext: "_Extraction", *, location: str | None = None
    ) -> bool:
        """Persist a mined salary/employer/geography onto the row; return whether anything wrote.

        Salary only fills a row that has none (``set_salary`` guards it, so a source figure
        always wins); a non-empty employer records the cleaner name and repoints the §16
        dedup key at its canonical form. A geo answer routes by string class (§4.12): a
        rules-'partial'/'fail' string fills the value LEDGER (paid once, reused by every
        row carrying the string); a bare-marker string's answer is POSTING-LOCAL (mined
        from this description). Both fills are first-writer-wins in the store. When the
        location arms are on, the row is stamped (``loc_llm_at``) even for an empty
        answer, so the marker arm never re-selects this content."""
        from jobcrawler.runtime.normalize import canonical_company

        wrote = False
        if ext.salary_min is not None:
            wrote |= self._store.set_salary(
                site,
                url,
                salary_min=ext.salary_min,
                salary_max=ext.salary_max,
                salary_currency=ext.salary_currency,
                salary_period=ext.salary_period,
            )
        if ext.hiring_company:
            self._store.set_hiring_company(
                site,
                url,
                name=ext.hiring_company,
                canonical=canonical_company(ext.hiring_company),
            )
            wrote = True
        if self._extract_locations:
            loc = (location or "").strip()
            geo_answer = ext.location_geo
            if geo_answer is not None and loc:
                model = cast(str, self._extract_model)
                if self._store.location_needs_llm(site, loc):
                    wrote |= self._store.set_location_value_llm(
                        site,
                        loc,
                        places=geo_answer.get("places") or [],
                        fence=geo_answer.get("fence") or [],
                        remote=geo_answer.get("remote") or "none",
                        model=model,
                    )
                elif parse_location(loc).quality == "marker":
                    wrote |= self._store.set_position_location_llm(
                        site,
                        url,
                        places=geo_answer.get("places") or [],
                        fence=geo_answer.get("fence") or [],
                        remote=geo_answer.get("remote") or "none",
                        model=model,
                    )
            self._store.mark_location_extracted(site, url)
        return wrote


# --- content hashing (mirror the §4.7 runner) ------------------------------


def _content_hash(job: object) -> str:
    """Hash a job's identity content — title/company/location/description.

    The SAME formula the §4.7 runner uses for ``positions.content_hash`` so a job's
    cache key is stable across the runner and the matcher: whitespace-normalized fields
    joined and sha256-hexed. Two postings with identical visible content share a key
    (and thus one verdict); any content edit re-hashes and re-scores.
    """
    parts = [
        _norm_ws(getattr(job, "title", "")),
        _norm_ws(getattr(job, "company", "")),
        _norm_ws(getattr(job, "location", "")),
        _norm_ws(getattr(job, "description", "")),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _norm_ws(value: object) -> str:
    """Collapse all runs of whitespace to single spaces and strip (hash stability)."""
    return " ".join(str(value or "").split())


# --- store-side drain: rebuild candidates from parked rows (§4.7) ----------
#
# The positions row persists the description, so a parked job can be re-presented to
# the LLM straight from the store — no crawl round-trip. This is what makes a profile
# edit actionable NOW: requeue the open backlog, rebuild each row into the same
# (site, url, enriched) triple the runner sends, and let the ordinary score_new path
# (cache → cap → batch → fail-open) do the rest.


@dataclass(frozen=True, slots=True)
class _RowSalary:
    """The stored salary columns, shaped like the §4.6 parsed range for the digest."""

    min: float | None
    max: float | None
    currency: str
    period: str


@dataclass(frozen=True, slots=True)
class _RowJob:
    """A job rebuilt from its positions row — the ``_JobLike`` fields, verbatim.

    ``salary`` is always None: the parsed range (kept in its own columns) is carried by
    ``_RowSalary`` on the enriched wrapper, and the raw salary *text* was never stored.
    The digest prefers the parsed range anyway, so nothing is lost.
    """

    title: str
    company: str
    location: str
    description: str
    employment_type: str | None
    department: str | None
    salary: str | None = None


@dataclass(frozen=True, slots=True)
class _RowEnriched:
    """A stored row wearing the ``_Enriched`` shape the digest/prefilter expect."""

    job: _RowJob
    discipline: str
    level: str
    workplace: str
    salary: _RowSalary | None


def candidate_from_row(rec: object) -> _RowEnriched | None:
    """Rebuild one parked row into an ``_Enriched`` candidate, or None if unscorable.

    None means the row cannot faithfully re-present the posting host-side and must wait
    for the next crawl to re-serve it (the pre-existing drain path):

      * **no stored description** — a row written before descriptions were persisted;
        the next crawl's upsert backfills it (every re-seen row refreshes its fields).
      * **content-hash mismatch** — the rebuilt job must hash to the row's own
        ``content_hash``, or a fresh verdict would be cached under a key no future
        lookup computes. This cannot happen for rows the current upsert wrote (hash and
        fields are written together from one Job); the guard is belt-and-braces against
        partially-migrated rows.

    Missing enrichment buckets degrade to ``"unspecified"`` — recall-safe: the
    prefilter passes them through to the LLM rather than inventing a rejection.
    """
    description = getattr(rec, "description", None)
    if not description:
        return None
    job = _RowJob(
        title=getattr(rec, "title", "") or "",
        company=getattr(rec, "company", "") or "",
        location=getattr(rec, "location", "") or "",
        description=description,
        employment_type=getattr(rec, "employment_type", None),
        department=getattr(rec, "department", None),
    )
    if _content_hash(job) != getattr(rec, "content_hash", None):
        return None
    salary_min = getattr(rec, "salary_min", None)
    salary_max = getattr(rec, "salary_max", None)
    salary = (
        _RowSalary(
            min=salary_min,
            max=salary_max,
            currency=getattr(rec, "salary_currency", None) or "",
            period=getattr(rec, "salary_period", None) or "",
        )
        if salary_min is not None or salary_max is not None
        else None
    )
    return _RowEnriched(
        job=job,
        discipline=getattr(rec, "discipline", None) or "unspecified",
        level=getattr(rec, "level", None) or "unspecified",
        workplace=getattr(rec, "workplace", None) or "unspecified",
        salary=salary,
    )


def drain_candidates(
    store: "StateStore", prefs: ProfilePrefs, *, limit: int
) -> tuple[list[tuple[str, str, _RowEnriched]], dict[str, int]]:
    """The parked backlog rebuilt into scoreable candidates, plus drain stats.

    Pulls up to ``limit`` pending rows (FIFO), rebuilds each via
    :func:`candidate_from_row`, and applies the free deterministic :func:`prefilter`.
    A row the prefilter now definitely rejects is UNPARKED without a verdict
    (``clear_match_pending``) so permanent mismatches can't clog every future drain
    window. The window is drawn with ``with_description_only`` so description-less rows
    (which rebuild to nothing) never occupy it — they stay parked for a re-crawl to
    backfill; the residual ``unscorable`` count here is the belt-and-braces
    content-hash-mismatch path in :func:`candidate_from_row`. The caller feeds the
    candidates to :meth:`Matcher.score_new`, which applies the cache, the daily cap, and
    fail-open parking as usual.
    """
    candidates: list[tuple[str, str, _RowEnriched]] = []
    unscorable = 0
    prefiltered = 0
    rows = store.pending_matches(limit=limit, with_description_only=True)
    for rec in rows:
        enriched = candidate_from_row(rec)
        if enriched is None:
            unscorable += 1
            continue
        keep, _why = prefilter(enriched, prefs)
        if not keep:
            store.clear_match_pending(rec.site, rec.url)
            prefiltered += 1
            continue
        candidates.append((rec.site, rec.url, enriched))
    stats = {
        "rows": len(rows),
        "unscorable": unscorable,
        "prefiltered": prefiltered,
    }
    return candidates, stats


# --- digest + verdict (de)serialization ------------------------------------


def _digest(index: int, enriched: _Enriched) -> dict[str, object]:
    """The compact per-job payload sent to the LLM (token economy, §8).

    Structured fields carry most of the fit signal; the description is truncated to
    1200 chars because the head (role summary + top requirements) dominates the signal
    and a full multi-KB posting would blow the batch's token budget for little gain.
    Salary prefers the §4.6 parsed range over the raw text (a number beats a phrase).
    """
    job = enriched.job
    salary: object
    if enriched.salary is not None:
        salary = {
            "min": enriched.salary.min,
            "max": enriched.salary.max,
            "currency": enriched.salary.currency,
            "period": enriched.salary.period,
        }
    elif getattr(job, "salary", None):
        salary = job.salary
    else:
        salary = None
    return {
        "index": index,
        "title": getattr(job, "title", None),
        "company": getattr(job, "company", None),
        "location": getattr(job, "location", None),
        "workplace": enriched.workplace,
        "employment_type": getattr(job, "employment_type", None),
        "salary": salary,
        "department": getattr(job, "department", None),
        "description": (getattr(job, "description", "") or "")[:_DIGEST_CHARS],
    }


# --- match-time extraction (§4.7) ------------------------------------------

# The salary periods the extractor is allowed to emit. An out-of-set period is dropped
# rather than guessed (never invent a unit) — matching the §4.6 host parse's conservatism.
_SALARY_PERIODS: frozenset[str] = frozenset({"year", "month", "hour"})


@dataclass(frozen=True, slots=True)
class _Extraction:
    """One posting's LLM-mined salary + employer + geography (§4.7).

    A salary is present only as a whole: ``salary_min`` is None whenever the description
    stated no usable figure OR no currency (an amount with no currency is ambiguous), and in
    that case every salary field is None. ``hiring_company`` is "" when the text named no
    clearer employer than the board already did. ``location_geo`` is the validated §4.12
    geo answer ({places, remote, fence} — vocabulary-checked against the gazetteer) or
    None when the posting stated no geography (or the model's answer was unusable).
    """

    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    hiring_company: str
    location_geo: dict[str, Any] | None = None


def _extract_digest(index: int, enriched: _Enriched) -> dict[str, object]:
    """The compact per-job payload sent to the extractor.

    A wider description window than the matcher's (2000 vs 1200 chars): salary and the
    real employer often sit deep in a posting (benefits block, "About <Company>" footer),
    past where the fit signal is decided. The board's ``location`` string and derived
    ``workplace`` ride along for the §4.12 geo answer — without them the model could
    only ever mine the description.
    """
    job = enriched.job
    description = getattr(job, "description", "") or ""
    if len(description) > 6000:
        # Pay lives in footers: on the measured gate pool, 0/88 stated ranges sat
        # inside a head-only 2000-char window and 88/88 inside head+tail. Send
        # the head and the tail, skip the middle.
        description = description[:2000] + "\n[…]\n" + description[-4000:]
    return {
        "index": index,
        "title": getattr(job, "title", None),
        "company": getattr(job, "company", None),
        "location": getattr(job, "location", None),
        "workplace": getattr(enriched, "workplace", None),
        "description": description,
    }


def _num_or_none(value: object) -> float | None:
    """Coerce an LLM salary bound to a positive float, else None (never invent a number)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            num = float(cleaned)
        except ValueError:
            return None
        return num if num > 0 else None
    return None


def _to_extraction(item: object) -> _Extraction | None:
    """Build an ``_Extraction`` from one parsed LLM object, defensively.

    Returns None only when ``item`` is not a mapping (unusable). A mapping with neither a
    salary nor a company is a valid "nothing found" and yields an empty ``_Extraction`` —
    the caller caches that (so the posting isn't re-billed) and persists nothing. Salary is
    kept ONLY with a usable amount AND a currency; an unrecognized period is dropped.
    """
    if not isinstance(item, dict):
        return None
    smin = smax = None
    currency = period = None
    salary = item.get("salary")
    if isinstance(salary, dict):
        lo = _num_or_none(salary.get("min"))
        hi = _num_or_none(salary.get("max"))
        lo, hi = (lo if lo is not None else hi), (hi if hi is not None else lo)
        cur = salary.get("currency")
        cur_norm = cur.strip().upper() if isinstance(cur, str) and cur.strip() else None
        if cur_norm is not None and not re.fullmatch(r"[A-Z]{3}", cur_norm):
            cur_norm = None  # 'US Dollars' is prose, not a code — refuse, don't coerce
        per = salary.get("period")
        per_norm = per.strip().lower() if isinstance(per, str) and per.strip() else None
        if per_norm is not None and per_norm not in _SALARY_PERIODS:
            # A STATED period outside year|month|hour (day/week) would need an
            # invented annualization factor — the whole figure refuses; an
            # ABSENT period is fine (set_salary defaults it by currency).
            lo = None
        if lo is not None and cur_norm is not None:
            smin, smax, currency = lo, hi, cur_norm
            period = per_norm
    company = item.get("hiring_company")
    hiring = company.strip() if isinstance(company, str) else ""
    return _Extraction(
        salary_min=smin,
        salary_max=smax,
        salary_currency=currency,
        salary_period=period,
        hiring_company=hiring,
        location_geo=_valid_location_geo(item.get("location_geo")),
    )


def _valid_location_geo(raw: object) -> dict[str, Any] | None:
    """Validate an LLM geo answer against the §4.12 vocabulary — drop junk, never coerce.

    Countries must be gazetteer ISO2 (case-folded to UPPER; anything else reads as
    unresolved, not as a guess); fence tokens must be ISO2 or a known macro key. An
    answer with no places, no fence, and no worldwide claim reads as None — absence
    of geography is recorded per-posting via ``loc_llm_at``, never as an empty fact.
    """
    from jobcrawler.runtime.geo import COUNTRY_CODES, MACRO_KEYS

    if not isinstance(raw, dict):
        return None
    remote_raw = raw.get("remote")
    remote = (
        remote_raw.strip().lower()
        if isinstance(remote_raw, str)
        and remote_raw.strip().lower() in ("none", "fenced", "worldwide")
        else "none"
    )
    places: list[dict[str, Any]] = []
    raw_places = raw.get("places")
    for entry in raw_places if isinstance(raw_places, list) else []:
        if not isinstance(entry, dict):
            continue
        city = entry.get("city")
        sub = entry.get("subdivision")
        country = entry.get("country")
        country_norm = (
            country.strip().upper()
            if isinstance(country, str) and country.strip().upper() in COUNTRY_CODES
            else None
        )
        place = {
            "city": city.strip() if isinstance(city, str) and city.strip() else None,
            "subdivision": (
                sub.strip() if isinstance(sub, str) and sub.strip() else None
            ),
            "country": country_norm,
        }
        if any(place.values()):
            places.append(place)
    fence: list[str] = []
    raw_fence = raw.get("fence")
    for token in raw_fence if isinstance(raw_fence, list) else []:
        if not isinstance(token, str) or not token.strip():
            continue
        text = token.strip()
        if text.upper() in COUNTRY_CODES:
            fence.append(text.upper())
        elif text.lower() in MACRO_KEYS:
            fence.append(text.lower())
    if not places and not fence and remote != "worldwide":
        return None
    return {"places": places, "remote": remote, "fence": fence}


def _parse_extraction_list(raw: object) -> list[object]:
    """Parse the LLM ``extractions`` output into a list, tolerating model chatter.

    Mirrors ``_parse_verdict_list``: raises ``ValueError`` on empty/non-JSON output so the
    batch fails open, tolerates code fences and surrounding prose (the first complete JSON
    value wins), and unwraps a ``{"extractions": [...]}`` object. Per-``index`` mapping
    upstream keeps salvage safe — an entry either names a job in the batch or is dropped.
    """
    if raw is None:
        raise ValueError("empty extractions output")
    text = _strip_fences(str(raw).strip())
    if not text:
        raise ValueError("empty extractions output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        data = _first_json_value(text)
        if data is None:
            raise ValueError(f"extractions output is not JSON: {exc}") from exc
    if isinstance(data, dict):
        inner = data.get("extractions")
        if isinstance(inner, list):
            return cast("list[object]", inner)
        return [data]
    if not isinstance(data, list):
        raise ValueError("extractions output is not a JSON array")
    return cast("list[object]", data)


def _extraction_to_json(ext: _Extraction) -> str:
    """Serialize an ``_Extraction`` for the extract cache (flat, one JSON object)."""
    return json.dumps(
        {
            "salary_min": ext.salary_min,
            "salary_max": ext.salary_max,
            "salary_currency": ext.salary_currency,
            "salary_period": ext.salary_period,
            "hiring_company": ext.hiring_company,
            "location_geo": ext.location_geo,
        },
        ensure_ascii=False,
    )


def _extraction_from_json(cached: object) -> _Extraction | None:
    """Rebuild an ``_Extraction`` from a cached JSON string; None if unusable."""
    if not isinstance(cached, str):
        return None
    try:
        data = json.loads(cached)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    smin = data.get("salary_min")
    smax = data.get("salary_max")
    return _Extraction(
        salary_min=float(smin) if isinstance(smin, (int, float)) else None,
        salary_max=float(smax) if isinstance(smax, (int, float)) else None,
        salary_currency=data.get("salary_currency")
        if isinstance(data.get("salary_currency"), str)
        else None,
        salary_period=data.get("salary_period")
        if isinstance(data.get("salary_period"), str)
        else None,
        hiring_company=str(data.get("hiring_company") or ""),
        # Re-validated on the way out of the cache: the vocabulary can only have
        # gotten stricter since the entry was written.
        location_geo=_valid_location_geo(data.get("location_geo")),
    )


def _verdict_label(score: int) -> str:
    """Derive a verdict band from a numeric score (the score is authoritative)."""
    if score >= 80:
        return "strong"
    if score >= 60:
        return "good"
    if score >= 40:
        return "weak"
    return "reject"


def _coerce_index(value: object) -> int | None:
    """Normalize an LLM-returned batch ``index`` to an int, or None if not numeric.

    The prompt asks for an integer ``index`` echoing each job's position, but a model may
    emit it as a string (``"3"``) or a float (``3.0``). All must map to the same int the
    batch loop looks up by ``enumerate`` — otherwise a whole batch of string-indexed
    verdicts misses on every lookup, parking every job AND re-charging the §8 daily cap on
    the next drain. Rejects bool (an ``index`` is never True/False, and ``bool`` is an
    ``int`` subclass that would otherwise collapse to 0/1)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None
    return None


def _to_verdict(item: object) -> MatchVerdict | None:
    """Build a :class:`MatchVerdict` from one parsed LLM object, defensively.

    Returns ``None`` for a malformed item (not a mapping, or a non-numeric score) so
    the caller parks that single job pending. The score is clamped to 0-100; an unknown
    or missing verdict label is derived from the score.
    """
    if not isinstance(item, dict):
        return None
    raw_score = item.get("score")
    if not isinstance(raw_score, (int, float, str)):
        return None
    try:
        score = int(round(float(raw_score)))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    label = item.get("verdict")
    if isinstance(label, str) and label.strip().lower() in _VERDICTS:
        verdict = label.strip().lower()
    else:
        verdict = _verdict_label(score)
    return MatchVerdict(
        score=score,
        verdict=verdict,
        reasons=_as_str_tuple(item.get("reasons")),
        concerns=_as_str_tuple(item.get("concerns")),
    )


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Coerce an LLM reasons/concerns field to a tuple of non-empty strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, Iterable):
        out = [str(v).strip() for v in value]
        return tuple(v for v in out if v)
    return ()


def _parse_verdict_list(raw: object) -> list[object]:
    """Parse the LLM ``verdicts`` output into a list, tolerating model chatter.

    Raises ``ValueError`` on empty output or output with no JSON value in it, so the
    batch fails open. Code fences, prose before/after the JSON (the most common
    malformation — and one that gets CACHED, replaying forever if treated as fatal),
    a bare object, and a ``{"verdicts": [...]}`` wrapper are all tolerated: the first
    complete JSON value found is used and the chatter ignored. The per-``index``
    mapping upstream keeps salvage safe — a verdict either names a job in the batch
    or is dropped.
    """
    if raw is None:
        raise ValueError("empty verdicts output")
    text = _strip_fences(str(raw).strip())
    if not text:
        raise ValueError("empty verdicts output")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        data = _first_json_value(text)
        if data is None:
            raise ValueError(f"verdicts output is not JSON: {exc}") from exc
    if isinstance(data, dict):
        inner = data.get("verdicts")
        if isinstance(inner, list):
            return cast("list[object]", inner)
        return [data]
    if not isinstance(data, list):
        raise ValueError("verdicts output is not a JSON array")
    return cast("list[object]", data)


def _first_json_value(text: str) -> object | None:
    """The first complete JSON value embedded in ``text``, or None if there is none.

    Chatty models lead or trail the requested JSON with prose; the value itself is
    usually intact. Try a ``raw_decode`` from each ``[``/``{`` until one parses —
    responses are a few KB, so the scan is cheap.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                value, _end = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            return value
    return None


def _strip_fences(text: str) -> str:
    """Strip a leading ```/```json fence and trailing ``` an LLM may wrap JSON in."""
    stripped = text.strip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        stripped = stripped[newline + 1 :] if newline != -1 else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _verdict_to_json(verdict: MatchVerdict) -> str:
    """Serialize a verdict for the match cache (round-trips via _verdict_from_json)."""
    return json.dumps(
        {
            "score": verdict.score,
            "verdict": verdict.verdict,
            "reasons": list(verdict.reasons),
            "concerns": list(verdict.concerns),
        }
    )


def _inherited_verdict(donor: PositionRecord) -> MatchVerdict:
    """A donor row's persisted verdict, rebuilt as a :class:`MatchVerdict` (§17).

    Callers guarantee ``donor.match_score`` is set. The band label falls back to the
    score-derived one if the stored label is missing (score is authoritative); the
    reasons/concerns JSON is parsed tolerantly — a corrupt list degrades to empty,
    never a raise (inheritance is an optimization, not a correctness dependency).
    """
    score = max(0, min(100, int(donor.match_score or 0)))
    return MatchVerdict(
        score=score,
        verdict=donor.match_verdict or _verdict_label(score),
        reasons=_stored_list(donor.match_reasons),
        concerns=_stored_list(donor.match_concerns),
    )


def _stored_list(raw: str | None) -> tuple[str, ...]:
    """Parse a persisted reasons/concerns JSON list; anything malformed is empty."""
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except ValueError:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(str(x) for x in data)


def _verdict_from_json(cached: object) -> MatchVerdict | None:
    """Reconstruct a cached verdict; ``None`` if the cache row is unparseable.

    A corrupt cache entry is treated as a miss (re-scored) rather than crashing — the
    cache is an optimization, never a correctness dependency.
    """
    if not isinstance(cached, str):
        return None
    try:
        data = json.loads(cached)
        score = max(0, min(100, int(data["score"])))
        return MatchVerdict(
            score=score,
            verdict=str(data["verdict"]),
            reasons=_as_str_tuple(data.get("reasons")),
            concerns=_as_str_tuple(data.get("concerns")),
        )
    except Exception:
        return None


def _chunks(
    seq: Sequence[tuple[str, str, _Enriched, str]], size: int
) -> Iterable[Sequence[tuple[str, str, _Enriched, str]]]:
    """Yield ``seq`` in contiguous ``size``-length slices (the batch windows)."""
    for start in range(0, len(seq), size):
        yield seq[start : start + size]
