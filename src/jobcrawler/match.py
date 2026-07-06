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
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterable, Protocol, Sequence, cast

import dspy

from jobcrawler.lm import build_lm
from jobcrawler.profile import Profile, ProfilePrefs

if (
    TYPE_CHECKING
):  # concrete import (no runtime coupling); StateStore has already landed
    from jobcrawler.state import StateStore

_log = logging.getLogger("jobcrawler.match")

# Bumped on ANY change to the MatchJobs prompt/rubric or digest shape: it is one of
# the four cache keys, so bumping it invalidates every prior verdict (a new rubric can
# score the same job differently, so old verdicts must not be reused). Never reuse a
# version string for a different prompt.
PROMPT_VERSION = "match-v1"

# The verdict bands. Numeric score is the source of truth; the label is derived from
# it when the LLM omits or garbles it (see _verdict_label). match_min_score (the
# notify threshold, host-side in config) is applied by the RUNNER, not here — the
# matcher is threshold-agnostic so §8 keeps the decision boundary off this surface.
_VERDICTS: frozenset[str] = frozenset({"strong", "good", "weak", "reject"})

# Discipline buckets that are recall-safe: never prefilter-dropped for "not in prefs"
# — they carry too little signal to reject deterministically, so the LLM decides.
_DISCIPLINE_PASS: frozenset[str] = frozenset({"eng_other", "unspecified"})


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
    ) -> None:
        self._store = store
        self._profile = profile
        self._model = model
        self._batch = max(1, batch)
        self._daily_cap = max(0, daily_cap)
        self._governor = governor
        self._clock = clock
        # A verdict is ~200 tokens; even a full batch fits comfortably in 4k. Bounding
        # the request matters beyond cost: providers reject a call whose max_tokens
        # exceeds the remaining credit balance, so an unbounded (provider-default)
        # ceiling turns a low-credit account into a hard outage for matching.
        self._max_tokens = max(256, max_tokens)
        # Constructed offline (no LM bound yet); the LM is set per batch under
        # dspy.context. Tests overwrite this attribute with a fake predictor.
        self._predict = dspy.Predict(MatchJobs)

    def score_new(
        self, candidates: Sequence[tuple[str, str, _Enriched]]
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
        """
        day = self._clock().date().isoformat()
        results: list[tuple[str, str, MatchVerdict]] = []
        cached_n = 0
        pending_n = 0

        # (a) cache lookup — a hit is free and applied at once; a miss queues for LLM.
        misses: list[tuple[str, str, _Enriched, str]] = []
        for site, url, enriched in candidates:
            content_hash = _content_hash(enriched.job)
            cached = self._store.cached_match(
                content_hash, self._profile.profile_hash, self._model, PROMPT_VERSION
            )
            verdict = _verdict_from_json(cached) if cached is not None else None
            if verdict is not None:
                self._apply(site, url, verdict)
                results.append((site, url, verdict))
                cached_n += 1
            else:
                misses.append((site, url, enriched, content_hash))

        # (c) daily cap — cache hits are free; only misses draw down the budget. Reserve
        # the grant ATOMICALLY (read + increment under one store lock) so a shared matcher
        # fanned out across concurrent ``asyncio.to_thread`` crawls can't each read a stale
        # spend and collectively overshoot the cap (the TOCTOU the old match_spent-then-late
        # -bump pair allowed). The grant is charged UP FRONT — a failed batch still burned
        # tokens — and the overflow is parked pending (never dropped) for a later run to drain.
        granted = (
            self._store.reserve_match_spend(day, len(misses), cap=self._daily_cap)
            if misses
            else 0
        )
        to_score = misses[:granted]
        for site, url, _enriched, _hash in misses[granted:]:
            self._store.set_match_pending(site, url)
            pending_n += 1

        # (b) batched LLM scoring of the reserved misses.
        scored_n = 0
        for batch in _chunks(to_score, self._batch):
            self._governor.pace()  # (d) pace the LLM calls
            try:
                applied = self._score_batch(batch)
            except Exception as exc:  # (e) fail-open — a bad batch is parked, not fatal
                _log.warning(
                    "match batch of %d job(s) failed, parking pending: %s: %s",
                    len(batch),
                    type(exc).__name__,
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
        return {
            "candidates": len(candidates),
            "cached": cached_n,
            "scored": scored_n,
            "pending": pending_n,
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
        by_index: dict[object, object] = {}
        for item in items:
            if isinstance(item, dict):
                by_index[item.get("index")] = item

        applied: list[tuple[str, str, MatchVerdict]] = []
        for index, (site, url, _enriched, content_hash) in enumerate(batch):
            verdict = _to_verdict(by_index.get(index))
            if verdict is None:
                self._store.set_match_pending(site, url)
                continue
            self._apply(site, url, verdict)
            self._store.cache_match(
                content_hash,
                self._profile.profile_hash,
                self._model,
                PROMPT_VERSION,
                _verdict_to_json(verdict),
            )
            applied.append((site, url, verdict))
        return applied

    def _apply(self, site: str, url: str, verdict: MatchVerdict) -> None:
        """Persist a verdict to the position row (clears its pending flag)."""
        self._store.set_match(
            site,
            url,
            score=verdict.score,
            verdict=verdict.verdict,
            reasons=json.dumps(list(verdict.reasons)),
        )


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
    window; an unscorable row (no stored description yet) is left parked for the next
    crawl to backfill. The caller feeds the candidates to :meth:`Matcher.score_new`,
    which applies the cache, the daily cap, and fail-open parking as usual.
    """
    candidates: list[tuple[str, str, _RowEnriched]] = []
    unscorable = 0
    prefiltered = 0
    rows = store.pending_matches(limit=limit)
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
        "description": (getattr(job, "description", "") or "")[:1200],
    }


def _verdict_label(score: int) -> str:
    """Derive a verdict band from a numeric score (the score is authoritative)."""
    if score >= 80:
        return "strong"
    if score >= 60:
        return "good"
    if score >= 40:
        return "weak"
    return "reject"


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
