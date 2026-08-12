"""Runtime configuration for jobcrawler, read from the environment (§11).

Every env knob the service reads lives here, so the full configuration surface
is visible in one place. All thresholds are host-side constants — never exposed
to the generator/RLM (the §8 hidden-scorer invariant). Getters are thin
wrappers over a few typed parsers.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from jobcrawler.durations import parse_duration
from jobcrawler.runtime.dedup import IDENTITY_APPLY_TRACKING_PARAMS
from jobcrawler.urls import (
    DEFAULT_MARKETPLACE_APPLY_HOSTS,
    DEFAULT_MARKETPLACE_COMPANIES,
    DEFAULT_TRACKING_PARAMS,
)

# This module lives at the package root, so its directory *is* the package dir —
# used to default the committed-crawlers location to the in-package crawlers/ dir.
_PKG_DIR = Path(__file__).parent

# --- parsers ---------------------------------------------------------------


def _str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _opt(name: str) -> str | None:
    return os.getenv(name) or None


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None else default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- models ----------------------------------------------------------------


def model() -> str | None:
    """RLM root planner/extractor — a strong, code-capable model. Defaults to
    ``chatgpt/gpt-5.6-sol``; set ``JOBCRAWLER_MODEL`` to override. The default is a
    real provider slug (not the old opaque-failing placeholder), so generation call
    sites can lean on :func:`require_model` and always get a usable model."""
    return _opt("JOBCRAWLER_MODEL") or "chatgpt/gpt-5.6-sol"


def require_model() -> str:
    """The root planner model, erroring if unset (§4.2/§11).

    ``JOBCRAWLER_MODEL`` names the provider model slug for ``dspy.LM``; there is no
    sensible default (the old ``openrouter/qwen/qwen3.7-plus`` was a non-existent
    slug that failed deep in litellm), so the generation/optimize path errors loud
    and early rather than surfacing an opaque provider 404 mid-run.
    """
    value = model()
    if value is None:
        raise RuntimeError(
            "JOBCRAWLER_MODEL is required (§4.2): the RLM root planner needs a real "
            "provider model slug (e.g. openrouter/<vendor>/<model>). Set it to a "
            "strong, code-capable model."
        )
    return value


def sub_model() -> str | None:
    """RLM ``sub_lm`` — the cheap in-sandbox model every ``llm_query`` bills against.
    Defaults to ``chatgpt/gpt-5.6-luna``; set ``JOBCRAWLER_SUB_MODEL`` to override. The
    default keeps in-sandbox queries off the pricier root planner (§4.2)."""
    return _opt("JOBCRAWLER_SUB_MODEL") or "chatgpt/gpt-5.6-luna"


def require_sub_model() -> str:
    """The ``sub_lm`` model, erroring if unset (§4.2/§11).

    An unset ``sub_lm`` silently falls back to the *root* planner LM inside
    ``dspy.RLM``, billing every in-sandbox ``llm_query`` at planner rates. The spec
    makes ``JOBCRAWLER_SUB_MODEL`` **required** precisely so this fails loud instead
    of quietly overspending — generation call sites must use this, never the
    ``sub_model() or model()`` fallback it replaces.
    """
    value = sub_model()
    if value is None:
        raise RuntimeError(
            "JOBCRAWLER_SUB_MODEL is required (§4.2): unset, every in-sandbox "
            "llm_query silently bills at the root planner's rate. Set it to a cheap, "
            "code-capable model (e.g. a small/instruct tier)."
        )
    return value


def reflection_model() -> str | None:
    """GEPA ``reflection_lm`` (strong, ideally a different family); §4.11/M5."""
    return _opt("JOBCRAWLER_REFLECTION_MODEL")


def require_reflection_model() -> str:
    """The GEPA ``reflection_lm`` model, erroring if unset (§4.11/M5).

    GEPA's whole loop is *reflective prompt mutation*: a strong LM reads each
    candidate's outputs + textual feedback and proposes a better instruction. It
    must be set (and is best a strong model from a *different* family than the
    planner, to avoid blind spots) — ``build_gepa`` requires a non-``None`` LM, so
    the optimize path errors loud here rather than constructing GEPA with no
    reflector. Only the ``gepa`` rung of the optimizer ladder reaches this; the
    bootstrap rung never needs a reflection model.
    """
    value = reflection_model()
    if value is None:
        raise RuntimeError(
            "JOBCRAWLER_REFLECTION_MODEL is required for the GEPA optimizer rung "
            "(§4.11): GEPA mutates the strategy prompt by reflecting on candidate "
            "feedback with this LM. Set it to a strong model, ideally a different "
            "family than JOBCRAWLER_MODEL."
        )
    return value


# --- ChatGPT (Codex) subscription OAuth (§9) -------------------------------
# Any of the model slugs above may be `chatgpt/<model>` instead of an
# `openrouter/...` slug: that routes through litellm's native ChatGPT provider —
# the Codex Responses backend billed against a ChatGPT subscription, authenticated
# by an OAuth token rather than an API key (see jobcrawler.lm / jobcrawler.auth).
# `jobcrawl login` imports that token from the Codex CLI's own ~/.codex/auth.json;
# this getter names where jobcrawler keeps its copy.


def auth_dir() -> str:
    """Directory holding the ChatGPT-subscription OAuth token (``auth.json``) a ``chatgpt/*``
    model slug authenticates from (§9).

    Resolves ``JOBCRAWLER_AUTH_DIR`` first, then litellm's own ``CHATGPT_TOKEN_DIR`` (so an
    operator already pointing litellm somewhere is honored), else a jobcrawler-owned default
    under ``~/.config``. :func:`jobcrawler.auth.apply_auth_env` pins ``CHATGPT_TOKEN_DIR`` to
    this value in-process so litellm's provider reads the same store ``jobcrawl login`` writes
    — a token store, not an API key, so it never appears in the provider-key line of §11."""
    return (
        os.getenv("JOBCRAWLER_AUTH_DIR")
        or os.getenv("CHATGPT_TOKEN_DIR")
        or os.path.expanduser("~/.config/jobcrawler/chatgpt")
    )


# --- profile & host-side matcher (§4.7) ------------------------------------
# The profile matcher is the ONLY new LLM surface, and it runs *host-side*,
# outside the crawl sandbox — batched and cache-keyed — so the committed crawl
# loop stays plain, deterministic Python (the no-LLM-in-the-loop invariant).
# Every knob here bounds that surface's token spend or its match strictness.


def profile_path() -> str:
    """Path to ``PROFILE.md`` — the operator's YAML-frontmatter + markdown profile the
    §4.7 matcher scores every job against. CWD-relative by default so a checkout is
    self-describing; :func:`jobcrawler.profile.load_profile` raises a create-me hint
    when it's absent (no profile ⇒ nothing to match, so the daemon must say so loudly
    rather than silently score against an empty preference set)."""
    return _str("JOBCRAWLER_PROFILE", "PROFILE.md")


def match_model() -> str | None:
    """The host-side §4.7 match LM slug. Defaults to ``chatgpt/gpt-5.6-luna`` (a cheap
    model — matching bills tokens per scored job); set ``JOBCRAWLER_MATCH_MODEL`` to
    override. Because the default is non-``None``, the §4.7 run loop's match gate treats
    matching as enabled out of the box (a deployment disables it by other means, e.g. no
    ``PROFILE.md``), rather than requiring an explicit match-model env var."""
    return _opt("JOBCRAWLER_MATCH_MODEL") or "chatgpt/gpt-5.6-luna"


def require_match_model() -> str:
    """The §4.7 matcher LM, erroring only if *no* usable model is configured at all.

    Matching bills real tokens per scored job, so it must run on a *cheap* model: this
    resolves ``JOBCRAWLER_MATCH_MODEL`` first, then falls back to the ``sub_lm``
    (:func:`sub_model`) — deliberately **never** the root planner (:func:`model`), whose
    per-token rate would make scoring the daily job fleet ruinous. It errors loud (like
    :func:`require_model`) only when neither is set, naming both env vars, rather than
    silently defaulting to an expensive or non-existent slug that fails opaque at the
    provider mid-batch.
    """
    value = match_model() or sub_model()
    if value is None:
        raise RuntimeError(
            "no match model configured (§4.7): set JOBCRAWLER_MATCH_MODEL for the "
            "host-side profile matcher, or JOBCRAWLER_SUB_MODEL (the cheap fallback it "
            "borrows). The matcher deliberately never falls back to the root "
            "JOBCRAWLER_MODEL planner — its per-token rate would make scoring the daily "
            "job fleet ruinous."
        )
    return value


def extract_model() -> str | None:
    """The §4.7 match-time description-extraction LM slug (``JOBCRAWLER_EXTRACT_MODEL``).

    Optional: unset, the salary/employer extractor borrows the cheap ``sub_lm`` via
    :func:`require_extract_model`, exactly as the matcher borrows it for scoring. Name a
    dedicated slug only to split extraction off from ``sub_lm``."""
    return _opt("JOBCRAWLER_EXTRACT_MODEL")


def require_extract_model() -> str:
    """The §4.7 description extractor LM, erroring only if *no* usable model is set at all.

    Extraction bills real tokens per salary-less job, so like the matcher it must run on a
    *cheap* model: resolves ``JOBCRAWLER_EXTRACT_MODEL`` first, then the ``sub_lm``
    (:func:`sub_model`) — deliberately **never** the root planner (:func:`model`). Errors
    loud (naming both env vars) only when neither is set.
    """
    value = extract_model() or sub_model()
    if value is None:
        raise RuntimeError(
            "no extract model configured (§4.7): set JOBCRAWLER_EXTRACT_MODEL for the "
            "host-side description extractor, or JOBCRAWLER_SUB_MODEL (the cheap fallback "
            "it borrows). Like the matcher it never falls back to the root JOBCRAWLER_MODEL."
        )
    return value


def match_batch() -> int:
    """How many jobs ride in ONE ``dspy.Predict(MatchJobs)`` call (§4.7). Batching
    amortizes the profile+rubric prompt prefix across many jobs (each job is a compact
    digest — the description head plus structured fields), the dominant token cost. Too
    large and a single malformed batch response strands more jobs to ``match_pending``
    on a length-mismatch; 10 balances prompt amortization against that blast radius."""
    return _int("JOBCRAWLER_MATCH_BATCH", 10)


def match_min_score() -> int:
    """The 0-100 score at or above which a match is worth an operator notification (§4.7
    'match' event). The matcher scores *everything* it is given and stores the verdict;
    this is purely the notify/surface threshold, not a drop — a 60 still lands in the
    store for the ``matches`` view, it just doesn't ping. Default 70 (a solid 'good'+
    fit); raise it for fewer, stronger alerts."""
    return _int("JOBCRAWLER_MATCH_MIN_SCORE", 70)


def score_half_life_days() -> float:
    """§4.7 freshness decay: the *effective* position score is the base match fit scaled by a
    recency multiplier that decays with the posting's age (from ``posted_at``, else
    ``first_seen``). This is the half-life in days — the multiplier's bonus above the floor
    halves every this-many days — so a newer posting outranks an equally-good older one.
    Larger = slower decay. Default 30d."""
    return _float("JOBCRAWLER_SCORE_HALF_LIFE_DAYS", 30.0)


def score_freshness_floor() -> float:
    """§4.7 freshness-decay floor in [0,1]: the smallest recency multiplier, so even a very old
    posting keeps this fraction of its fit — a **great fit stays represented**, never decaying
    to nothing. ``1.0`` disables decay (effective == base); ``0.6`` (default) lets a stale
    posting fall to 60% of its raw score at most."""
    return _float("JOBCRAWLER_SCORE_FRESHNESS_FLOOR", 0.6)


def match_daily_cap() -> int:
    """Hard per-UTC-day ceiling on the number of jobs sent to the match LM — the
    token-budget backstop (§4.7/§8). Only *LLM-scored* jobs count: a cache hit (a job
    whose ``content_hash`` + ``profile_hash`` was already scored) is FREE and never
    charged against it. Jobs past the cap are ``set_match_pending``, NEVER dropped — a
    later run or the recheck loop drains them within budget. Default 2000: bounds a bad
    crawl or a fresh-profile rematch storm from blowing the daily spend. Raise it only
    with the token bill in view."""
    return _int("JOBCRAWLER_MATCH_DAILY_CAP", 2000)


def match_drain_floor() -> int:
    """Slice of ``match_daily_cap`` held in reserve for the §4.7 backlog drain, so a day
    of heavy fresh inflow can't starve it (§4.7). Fresh crawl-time scoring may spend at
    most ``daily_cap - drain_floor``; the drain (and profile-edit rematch) may spend the
    remaining floor plus whatever fresh left unused. Without it, a crawl sweep that hits
    the cap leaves the drain ``granted=0`` every tick and the parked backlog never shrinks.
    Default 160 (20% of the 800 cap): raise it to drain backlog faster at the cost of fresh
    scoring headroom, set 0 to restore the single shared budget. Clamped to the cap."""
    return max(0, _int("JOBCRAWLER_MATCH_DRAIN_FLOOR", 160))


def match_concurrency() -> int:
    """Concurrent §4.7 scoring batches in flight. The rate governor still paces
    every batch START (thread-safe), so this overlaps model LATENCY without
    raising the request rate — with a 20/m rate and ~20-30s verdict latencies a
    sequential loop wastes most of its budget waiting on one call at a time.
    Default 4; 1 restores the sequential loop."""
    return max(1, _int("JOBCRAWLER_MATCH_CONCURRENCY", 4))


def match_rate() -> str:
    """Governor pacing for the §4.7 match LM calls (``requests/interval``, e.g. the
    default ``20/m``). Smooths the batched match calls so a big new-position sweep can't
    burst the provider into rate-limit errors; it paces *calls*, orthogonal to the daily
    token cap which bounds *volume*. Same governor grammar as the crawl rate limit."""
    return _str("JOBCRAWLER_MATCH_RATE", "20/m")


def match_max_tokens() -> int:
    """Per-request completion ceiling for the match LM, distinct from the generation
    LM's ``JOBCRAWLER_MAX_TOKENS`` (crawler code needs tens of KB; a verdict batch is
    ~200 tokens per job). Bounding it matters beyond cost: providers reject any call
    whose ``max_tokens`` exceeds the remaining credit balance, so an unbounded ceiling
    turns a low-credit account into a total matching outage. Default 4000 fits a full
    default batch with headroom."""
    return _int("JOBCRAWLER_MATCH_MAX_TOKENS", 4000)


def extract_min_score() -> int:
    """The 0-100 match score at or above which a salary-less posting is worth mining on the
    extract LM (§4.7 match-time extraction). Extraction is a *convenience* enrichment, not a
    fit signal — spending tokens to recover the salary of a job the profile already rejected
    is waste, so this gates the pass below the notify threshold (``match_min_score``, 70) but
    above the noise floor. Default 60 (a 'good' fit): raise it to mine only strong matches,
    lower it to fill more rows at more token cost."""
    return _int("JOBCRAWLER_EXTRACT_MIN_SCORE", 60)


def extract_cap() -> int:
    """Hard per-run ceiling on *fresh* (cache-miss) description extractions (§4.7). A cache
    hit re-applies for free and is never charged; only postings mined anew this run count.
    Bounds the token spike when a first pass meets a large salary-less backlog — the overflow
    is simply retried next run, nothing is parked. Distinct from ``match_daily_cap``: that
    bounds scoring volume per UTC day, this bounds extraction volume per run. Default 50."""
    return _int("JOBCRAWLER_EXTRACT_CAP", 50)


def extract_locations() -> bool:
    """The §4.12 location arms of the extract gate: the ledger tail (strings the rules
    parser left 'partial'/'fail' — score-independent, deduped per distinct string) and
    the marker arm (bare-'Remote' rows whose fence lives only in the description).
    Off ⇒ the gate reverts to salary-only and no location data is written or stamped by
    the pass; the deterministic parser and its projections are unaffected. Default on —
    the tail is small by construction (~3% of rows) and each string bills at most once."""
    return _bool("JOBCRAWLER_EXTRACT_LOCATIONS", True)


# --- generation caps (§4.2) ------------------------------------------------


def max_iterations() -> int:
    return _int("JOBCRAWLER_MAX_ITERATIONS", 20)


def max_llm_calls() -> int:
    return _int("JOBCRAWLER_MAX_LLM_CALLS", 40)


def max_subcalls() -> int:
    return _int("JOBCRAWLER_MAX_SUBCALLS", 60)


def fanout_token_cap() -> int:
    """The §4.2 aggregate fan-out token ceiling per site (prompt + completion).

    The fan-out is bounded by a *token* budget rather than a dollar figure: dspy records
    per-candidate token usage for every provider, whereas USD pricing is not universally
    available (litellm has no cost map for many OpenRouter / Codex slugs, and dspy never
    records ``response_cost`` regardless) — a dollar cap silently read as $0 and never
    fired. The cumulative tokens across candidates stop the fan-out once they reach this
    cap (after keeping the best so far), so a long tail of expensive candidates can't run
    away. Parses a human-friendly ``2M/site`` / ``1500k`` / bare-integer form; size it
    against the p95 tail, not the mean (§4.2). Default 2,000,000 tokens/site.
    """
    core = _str("JOBCRAWLER_FANOUT_TOKEN_CAP", "2M/site").strip().split("/")[0].strip()
    lowered = core.lower()
    mult = 1
    if lowered.endswith("k"):
        mult, lowered = 1_000, lowered[:-1].strip()
    elif lowered.endswith("m"):
        mult, lowered = 1_000_000, lowered[:-1].strip()
    return int(float(lowered) * mult)


def fanout_n() -> int:
    """Number of candidates the §6 best-of-N generates (default 2).

    Each candidate is a full ~20-call RLM trajectory, so N is the dominant factor in
    an attempt's LM bill. 2 keeps one comparison pair — enough for the argmax to
    reject a degenerate draw — at half the spend of the earlier default of 4; raise
    it back for quality-critical reruns."""
    return _int("JOBCRAWLER_FANOUT_N", 2)


def fanout_retries() -> int:
    """Extra draws for fan-out attempts that die before producing a prediction
    (an LM/adapter failure such as unparseable output — not a graded-zero
    candidate). Defaults to ``fanout_n()``: one full replacement round, so a
    model whose responses sometimes fail to parse still fills its comparison
    slots, while total attempts stay bounded at ``2 * n``.
    """
    return _int("JOBCRAWLER_FANOUT_RETRIES", fanout_n())


def max_tokens() -> int:
    """LM output-token cap. Generous by default so a reasoning model has room for
    both its thinking and the answer on the long RLM action prompt — too low and
    a reasoning model spends the whole budget thinking and returns empty content.
    """
    return _int("JOBCRAWLER_MAX_TOKENS", 16000)


def corpus_homepage_chars() -> int:
    """Cap on homepage HTML kept in the §9 snapshot. Raw pages run to 100s of KB;
    unbounded, the corpus blows past the model's context and a reasoning LM burns
    its whole token budget (empty output). Link discovery still uses the full page."""
    return _int("JOBCRAWLER_CORPUS_HOMEPAGE_CHARS", 60000)


def corpus_article_chars() -> int:
    """Cap on each article sample kept in the snapshot (enough to learn the body
    selector; a content-heavy page is otherwise 100s of KB of cruft)."""
    return _int("JOBCRAWLER_CORPUS_ARTICLE_CHARS", 20000)


# --- sites & crawlers ------------------------------------------------------


def sites_path() -> str:
    return _str("JOBCRAWLER_SITES", "sites.yaml")


def crawlers_dir() -> str:
    """Directory of committed per-site crawlers — ``crawlers/<slug>.py`` plus the
    §14.2 ``_pending`` staging area. Read by crawl/probe, written by
    generate/promote and the §4.8 self-heal.

    Defaults to the in-package ``crawlers/`` dir, where the editable dev install
    resolves them and the baked example crawler lives. A production deploy must point
    ``JOBCRAWLER_CRAWLERS_DIR`` at a writable, persistent path (e.g. a mounted
    ``/data/crawlers``): the in-package dir is read-only on a native Nix-store install
    (so generate/promote/self-heal can't write) and ephemeral in a container layer (so
    generated crawlers vanish on restart). The crawl loader reads the script *text*
    (never imports the package), so an out-of-package dir works for the whole
    crawl/probe/generate/promote surface.
    """
    return _str("JOBCRAWLER_CRAWLERS_DIR", str(_PKG_DIR / "crawlers"))


def first_crawl_cap() -> int:
    """On a site's first-ever position-yielding crawl, only the newest ``cap`` jobs (by
    ``posted_at``, discovery order as fallback) go to the §4.7 matcher now; the backlog
    tail is ``set_match_pending`` and drained by later runs. A brand-new board can list
    hundreds of standing roles — without this, a first crawl would burn the whole
    ``match_daily_cap`` scoring stale backlog instead of fresh postings. Default 30."""
    return _int("JOBCRAWLER_FIRST_CRAWL_CAP", 30)


# --- crawl / politeness (§4.5) ---------------------------------------------


def state_url() -> str:
    return _str("JOBCRAWLER_STATE_URL", "sqlite:///jobcrawler.db")


def run_lock_path() -> str:
    """Single-instance lockfile for the §4.9 scheduler daemon (held for its lifetime).

    ``jobcrawl run`` takes an advisory lock on this file so only one scheduler runs
    against a store at a time. ``cmd_run`` reaps every unfinished run row as a crash
    orphan on that single-process assumption; a second concurrent daemon would reap
    the first's in-flight runs and double-dispatch every site, so the guard is
    load-bearing. CWD-relative by default, like the optimize lock."""
    return _str("JOBCRAWLER_RUN_LOCK", "run.lock")


def user_agent() -> str:
    return _str("JOBCRAWLER_USER_AGENT", "jobcrawler/0.1 (+job tracker)")


def rate_limit() -> str:
    return _str("JOBCRAWLER_RATE_LIMIT", "2/s/host")


def max_pages() -> int:
    return _int("JOBCRAWLER_MAX_PAGES", 200)


def fetch_timeout() -> float:
    """Per-request HTTP timeout for a production crawl's fetches (§4.5).

    Bounds a single hung fetch so one stuck socket can't eat the whole subprocess
    wall-clock budget (:func:`crawl_timeout`); kept well under it. The validation
    sandbox keeps its own tight default — this is the live crawl/robots client."""
    return _float("JOBCRAWLER_FETCH_TIMEOUT", 20.0)


def fetch_retries() -> int:
    """Extra attempts after the first for a *transient* fetch failure (§4.5) — a
    429/503/502/504 or a dropped connection. ``0`` disables retry. A Cloudflare
    challenge is never counted here (it escalates to the solver, not a blind retry)."""
    return _int("JOBCRAWLER_FETCH_RETRIES", 2)


def fetch_backoff() -> float:
    """Base seconds for the exponential backoff (base·2ⁿ) between transient retries."""
    return _float("JOBCRAWLER_FETCH_BACKOFF", 0.5)


def fetch_retry_cap() -> float:
    """Ceiling on any single retry wait (§4.5), in seconds. Also the longest server
    ``Retry-After`` we'll honor: a larger one is refused rather than parking the crawl
    budget, and the response is surfaced as an error instead."""
    return _float("JOBCRAWLER_FETCH_RETRY_CAP", 30.0)


# --- committed-crawl isolation (§4.4) --------------------------------------
# A committed crawler runs on the production schedule in an isolated subprocess
# (rlimits + wall-clock timeout) so a runaway one can't take down the daemon.


def crawl_timeout() -> float:
    """Wall-clock ceiling for one committed crawl's subprocess (§4.4).

    Generous by default: a polite crawl spends most of its wall-clock *asleep*
    between paced fetches, so the wall-clock budget must cover ``max_pages`` ×
    per-host interval plus fetch latency. It bounds a *hung* crawl (a stuck socket,
    a sleep); a compute spin is caught sooner and tighter by the CPU cap. Raise it
    if you crawl large sites under slow per-host pacing."""
    return _float("JOBCRAWLER_CRAWL_TIMEOUT", 600.0)


def crawl_cpu_seconds() -> float:
    """RLIMIT_CPU (CPU-seconds) for one committed crawl's subprocess (§4.4).

    Decoupled from :func:`crawl_timeout`: a legitimate crawl is I/O-bound and burns
    little CPU even over a long wall-clock, so this stays tight to kill a compute
    spin (an infinite ``while`` loop blows past any finite CPU budget) without
    mistaking a slow-but-paced crawl for a runaway."""
    return _float("JOBCRAWLER_CRAWL_CPU_SECONDS", 120.0)


def crawl_mem_mb() -> int:
    """RLIMIT_AS (MB) for one committed crawl's subprocess — caps a memory blowup
    so one bad crawler can't OOM the daemon. Matches the validation sandbox."""
    return _int("JOBCRAWLER_CRAWL_MEM_MB", 1024)


def tracking_param_denylist() -> frozenset[str]:
    raw = os.getenv("JOBCRAWLER_TRACKING_PARAM_DENYLIST")
    if raw is None:
        return DEFAULT_TRACKING_PARAMS
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def marketplace_apply_host_denylist() -> frozenset[str]:
    """Hosts a posting's effective apply target is dropped for at ingestion (§4.7).

    Talent-marketplace / staffing-pool hosts (Proxify, Lemon, Toptal…) where applying
    means joining a candidate pool, not a specific employer — so the "job" has no real
    per-employer link and only pollutes the queue. ``JOBCRAWLER_MARKETPLACE_HOST_DENYLIST``
    (comma-separated) overrides the curated default; empty string disables the filter.
    """
    raw = os.getenv("JOBCRAWLER_MARKETPLACE_HOST_DENYLIST")
    if raw is None:
        return DEFAULT_MARKETPLACE_APPLY_HOSTS
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def marketplace_company_denylist() -> frozenset[str]:
    """Canonical company names a posting is dropped for at ingestion (§4.7).

    The host denylist above misses the boards' re-lists: those carry the marketplace
    as the *hiring company* while the apply link points back at the board itself, at
    nothing, or at a URL shortener (lemon.io arrived all three ways). Values are
    ``normalize.canonical_company`` keys ("Lemon.io" → "lemonio").
    ``JOBCRAWLER_MARKETPLACE_COMPANY_DENYLIST`` (comma-separated) overrides the
    curated default; empty string disables the filter.
    """
    raw = os.getenv("JOBCRAWLER_MARKETPLACE_COMPANY_DENYLIST")
    if raw is None:
        return DEFAULT_MARKETPLACE_COMPANIES
    return frozenset(c.strip().lower() for c in raw.split(",") if c.strip())


# --- gate thresholds (§6; M1 placeholders, calibrate per §14 Q8) ------------


def min_items() -> int:
    """Minimum positions a crawl must yield to clear the §6 completeness gate. Default 1
    (dropped from the old 5): a two-role startup careers page is a legitimate, complete
    board, so an absolute floor of 1 only rejects the truly empty crawl. Completeness is
    instead carried by the relative §6 enum-floor (did we get roughly as many as the
    board advertises?) and the run-to-run stability gate — both of which a genuinely
    small board passes and a broken selector fails."""
    return _int("JOBCRAWLER_MIN_ITEMS", 1)


def min_description_chars() -> int:
    """Length floor (chars) a job description must clear to count as complete (§4.6/§6).
    Default 300 (down from the news-era 400): a tight but real engineering posting — a
    few responsibilities plus requirements — clears 300, while a bare title-only stub or
    a "see our site" redirect does not. Shared by the §6 generation gate and the runtime
    completeness filter, so generation is never stricter than what runtime keeps."""
    return _int("JOBCRAWLER_MIN_DESCRIPTION_CHARS", 300)


def min_description_quality() -> float:
    """Fraction of sampled descriptions that must clear ``min_description_chars`` for the
    §6 completeness gate to pass. < 1.0 tolerates a minority of genuinely-terse postings
    (a one-line contract role), which the runtime completeness filter drops at ingest
    anyway — so generation isn't stricter than runtime. A crawler that scrapes only
    title + teaser instead of the full description still fails."""
    return _float("JOBCRAWLER_MIN_DESCRIPTION_QUALITY", 0.6)


def stability_jaccard() -> float:
    return _float("JOBCRAWLER_STABILITY_JACCARD", 0.6)


def extra_hosts_cap() -> int:
    """Max EXTRA_HOSTS a generated crawler may declare (§8). A real cross-host board
    needs exactly one (its platform's API host), so the default leaves headroom
    without opening a wide envelope: the gate-1(b) evidence rule is the
    *authorization*; this cap is the blast-radius bound on top, keeping an injected
    "declare everything the page names" instruction from turning one crawler into a
    broad fetcher. Host-side only, never surfaced to the generator."""
    return _int("JOBCRAWLER_EXTRA_HOSTS_CAP", 3)


def enum_floor_ratio() -> float:
    return _float("JOBCRAWLER_ENUM_FLOOR_RATIO", 0.5)


# --- completeness / freshness (§4.6) ---------------------------------------
# Runtime filters on a job's description, applied at ingest before it is hashed
# and stored. Host-side only (§8): the generator never sees these thresholds, so
# it can't tune a crawler to just barely clear them.


def min_unique_description_chars() -> int:
    """Minimum count of *distinct* characters a description must contain to count as
    real content (§4.6). Catches a description that clears ``min_description_chars`` on
    length alone but is boilerplate padding — a repeated company blurb, a wall of one
    punctuation char, an ATS placeholder — where the set of unique characters stays
    tiny. Default 150: high enough to demand genuinely varied prose, low enough that any
    real multi-sentence posting passes. Conservative — a borderline real posting is
    kept, not dropped."""
    return _int("JOBCRAWLER_MIN_UNIQUE_DESCRIPTION_CHARS", 150)


# Substrings (case-insensitive) that mark a posting as already closed even though the
# board still serves the page — a race the enumerate-diff closure (§4.7) can't catch on
# its own, since the URL is still present. A description carrying one is dropped at
# ingest as 'expired'. EN + pt-BR defaults from live boards; extend/override per
# deployment. Mirrors the old paywall-marker pattern this replaces.
_DEFAULT_EXPIRED_MARKERS = (
    "no longer accepting applications",
    "this position has been filled",
    "position is no longer available",
    "vaga encerrada",
    "vaga indisponível",
)


def expired_markers() -> tuple[str, ...]:
    """Closed-posting markers that drop a job at ingest even when the board still lists
    it (§4.6/§4.7). A page can outlive the opening (an ATS keeps serving "this position
    has been filled"); the enumerate-diff closure only fires when the URL *disappears*,
    so these catch the still-present-but-dead case. Override with
    ``JOBCRAWLER_EXPIRED_MARKERS`` (``|``-separated; empty disables)."""
    raw = os.getenv("JOBCRAWLER_EXPIRED_MARKERS")
    if raw is None:
        return _DEFAULT_EXPIRED_MARKERS
    return tuple(m.strip() for m in raw.split("|") if m.strip())


# --- position lifecycle: closure & recheck (§4.7, §4.9) --------------------
# How an open position ages into 'closed', and how sources that don't enumerate
# their whole board (``enumerates_all: false`` — an RSS/latest-N feed) get their
# standing rows re-verified out of band. Host-side (§8).


def close_after_misses() -> int:
    """Consecutive crawls a position must be ABSENT from an enumerating board before it
    is closed (§4.7 ``close_missing`` threshold). Default 2: a single miss is often a
    transient hiccup — a paginated board that briefly drops a page, an ATS 500 — so one
    absence only increments the row's ``missing_streak``; the second confirms the role
    is genuinely gone before flipping it to 'closed'. Only enumerating sources
    (``enumerates_all``) diff this way, and only after a successful, non-empty crawl (the
    §4.7 mass-closure guard), so this can never mass-close on a failed fetch."""
    return _int("JOBCRAWLER_CLOSE_AFTER_MISSES", 2)


def recheck_interval() -> str:
    """How often the §4.9 recheck loop re-fetches standing open positions to confirm
    they're still live. This is the ONLY closure signal for non-enumerating sources
    (``enumerates_all: false`` — an RSS or latest-N feed, where the diff is unsound
    because the source only ever shows a recent window); it also re-verifies enumerating
    rows against ``expired_markers`` / ``expires_at``. Default 12h — jobs churn on the
    order of days, so twice-daily is fresh without hammering a board."""
    return _str("JOBCRAWLER_RECHECK_INTERVAL", "12h")


def fx_endpoint() -> str:
    """Currency-rate endpoint the §4.6 FX refresh pulls (:mod:`jobcrawler.fx`).

    A keyless, USD-based JSON feed (default: exchangerate-api.com's open endpoint) read
    out-of-band — never on the crawl path — so host-side salary→USD stays a pure, offline
    function. The response is USD-based (1 USD = N units of X); the fetcher inverts to
    store USD-per-unit. Override to point at a mirror or a keyed provider."""
    return _str("JOBCRAWLER_FX_ENDPOINT", "https://open.er-api.com/v6/latest/USD")


def fx_refresh_interval() -> str:
    """How often the daemon refreshes currency exchange rates (§4.6). Default 24h — the
    upstream feed updates once daily and coarse salary ranking tolerates FX drift, so a
    daily pull is ample; ``off`` disables the loop (rates then only move via a manual
    ``jobcrawl fx-refresh``, falling back to the static table until first run)."""
    return _str("JOBCRAWLER_FX_REFRESH_INTERVAL", "24h")


def recheck_cap() -> int:
    """Max standing positions the §4.9 recheck loop re-fetches per pass. Bounds the extra
    fetch (and solver/politeness) budget the loop spends on already-known rows so a fleet
    with thousands of open positions doesn't starve fresh crawls; the oldest-checked rows
    go first, so all rows are covered over successive passes. Default 300."""
    return _int("JOBCRAWLER_RECHECK_CAP", 300)


def recheck_stale_after() -> str:
    """How long a full-enumeration board may go without a successful crawl before the
    §4.9 recheck sweep adopts its open rows. The runner's guarded diff (§4.7) only
    closes rows while crawls succeed — a board whose crawler broke, whose host started
    blocking, or whose health state parked it stops diffing, and its open rows would
    rot forever. Past this window the sweep re-verifies them per-URL like a windowed
    source (closing only on a definitive 404/410/expired-marker) until the diff comes
    back. Default 3d — a couple of failed dailies is routine flake, three days of no
    successful crawl means the diff is genuinely not protecting the rows; ``off``
    restores the old diff-only contract (stalled boards then never re-verified)."""
    return _str("JOBCRAWLER_RECHECK_STALE_AFTER", "3d")


def match_drain_interval() -> str:
    """How often the daemon's §4.7 match-drain tick runs: reload ``PROFILE.md`` (a hash
    change requeues the open backlog for rescoring) and re-present parked pending rows
    to the matcher straight from the store (their descriptions are persisted, so no
    crawl round-trip). Default 10m — fast enough that a profile edit feels live, cheap
    because an empty backlog is a no-op and every LLM call stays under the daily cap.
    ``off`` disables the tick (pending then drains only via re-crawls, the old way)."""
    return _str("JOBCRAWLER_MATCH_DRAIN_INTERVAL", "10m")


def heartbeat_interval() -> str:
    """How often the §4.9 run loop emits a liveness heartbeat when it would otherwise be
    silent. The loop logs only when it does work — a dispatched crawl, a sweep with
    counters — so a quiet daemon (nothing due, an idle roster) prints nothing for hours,
    and an operator watching the log can't tell "idle and healthy" from "wedged mid-tick".
    A periodic heartbeat line breaks that ambiguity: while it keeps arriving the loop is
    turning; when it stops, the loop is stuck. It also reports whether the §4.7 match-drain
    and §18 autofill drains — which ride their OWN tasks — are still alive, so a dead
    scoring/autofill queue surfaces even while the main loop spins. Default 5m — frequent
    enough to notice a wedge within minutes, sparse enough that a busy daemon's log gains
    only one line per interval; ``off`` disables it (for operators who monitor liveness by
    other means)."""
    return _str("JOBCRAWLER_HEARTBEAT_INTERVAL", "5m")


def match_drain_cap() -> int:
    """Max parked rows one match-drain tick re-presents to the matcher. Bounds a single
    tick's LLM exposure after a profile edit requeues thousands of rows; successive
    ticks (FIFO) work through the rest, and the §8 daily cap still bounds total spend.
    Default 200."""
    return _int("JOBCRAWLER_MATCH_DRAIN_CAP", 200)


# --- auto-apply prefill (§18) -------------------------------------------------
# The §18 worker drives the operator's OWN Chrome over CDP and bills LLM tokens per
# round, so every knob here either bounds that spend or points at operator-local
# resources (browser endpoint, resume file). Runs are operator-initiated (a web
# button press) — there is no autonomous trigger to cap.


def autofill_enabled() -> bool:
    """Master switch for the §18 auto-apply prefill drain (default on). Off ⇒ the
    daemon never opens Chrome or calls the LLM for prefill; the web button still
    queues rows, which simply wait (and survive) until the switch is on again."""
    return _bool("JOBCRAWLER_AUTOFILL", True)


def autofill_model() -> str | None:
    """Dedicated §18 form-navigation LM slug (``JOBCRAWLER_AUTOFILL_MODEL``). Optional:
    unset, the worker borrows the §4.7 match model chain via
    :func:`require_autofill_model` (form-filling is bursty and operator-initiated, so
    the cheap match-tier model is the right default)."""
    return _opt("JOBCRAWLER_AUTOFILL_MODEL")


def require_autofill_model() -> str:
    """The §18 prefill LM, erroring only if *no* usable model is configured at all.

    Resolves ``JOBCRAWLER_AUTOFILL_MODEL`` first, then the §4.7 match chain
    (:func:`require_match_model`: match model → ``sub_lm``) — deliberately never the
    root planner, mirroring the matcher's cost posture."""
    return autofill_model() or require_match_model()


def autofill_max_tokens() -> int:
    """Per-round completion budget for the §18 prefill LM. A round emits a JSON action
    list over every visible field, so it needs more headroom than a match verdict;
    default 6000. (Dropped for ``chatgpt/*`` slugs, like every build_lm cap.)"""
    return _int("JOBCRAWLER_AUTOFILL_MAX_TOKENS", 6000)


def autofill_max_rounds() -> int:
    """Max look→act rounds one §18 run may spend on a page (default 6). Each round is
    one LLM call over a fresh page digest; the cap bounds a form the model can't
    finish (infinite validation loops, unreachable fields) to a fixed spend."""
    return _int("JOBCRAWLER_AUTOFILL_MAX_ROUNDS", 6)


def autofill_poll() -> float:
    """Seconds between the daemon's §18 queue polls (default 3). The drain runs on its
    own scheduler task (like the §4.7 match drain) so a button press feels live even
    mid-crawl; the poll is one indexed SELECT, so a short cadence is cheap."""
    return _float("JOBCRAWLER_AUTOFILL_POLL", 3.0)


def autofill_cdp_url() -> str:
    """HTTP endpoint of the operator's Chrome remote-debugging port the §18 worker
    connects to (default ``http://127.0.0.1:9222`` — Chrome's
    ``chrome://inspect/#remote-debugging`` toggle). The worker drives the operator's
    real, logged-in browser profile; Chrome may show an "Allow debugging" prompt on
    the daemon's first connection per browser session."""
    return _str("JOBCRAWLER_AUTOFILL_CDP", "http://127.0.0.1:9222")


def autofill_resume_path() -> str | None:
    """Path to the resume/CV file the §18 worker may attach to a form's file input
    (``JOBCRAWLER_AUTOFILL_RESUME``, default ``CV.pdf`` — the repo-root convention,
    like ``PROFILE.md``/``APPLY.md``; silently unavailable when the file is absent,
    so upload actions report "no resume configured" and the field is left for the
    operator). Only THIS configured path ever reaches ``DOM.setFileInputFiles`` —
    the LLM cannot name a file."""
    return _str("JOBCRAWLER_AUTOFILL_RESUME", "CV.pdf")


def autofill_notes_path() -> str:
    """Optional markdown file of operator-provided application answers appended to the
    §18 prefill context (default ``APPLY.md``, silently skipped when absent): phone,
    LinkedIn/GitHub URLs, salary expectation, notice period — the form-only facts
    that don't belong in ``PROFILE.md``'s matching profile."""
    return _str("JOBCRAWLER_AUTOFILL_NOTES", "APPLY.md")


# --- identity overlay / dedup (§17) -----------------------------------------
# Pure host-side string mechanics: keys and clusters are *derived* state, rebuilt
# from immutable positions rows by a deterministic sweep — never an LLM call.


def dedup_enabled() -> bool:
    """Master switch for the §17 identity overlay: per-row key extraction at ingest,
    the periodic resweep, and every consumer below (which all compose this). Off ⇒
    the derived tables stop being maintained and match/notify/UI behave exactly
    pre-§17; ``positions`` rows are never touched either way, so flipping this is
    lossless in both directions. Default on."""
    return _bool("JOBCRAWLER_DEDUP", True)


def dedup_refresh_interval() -> str:
    """How often the daemon's §17 sweep rebuilds the identity overlay (drop the
    derived key/cluster tables, recompute keys, recluster, re-elect canonicals —
    disposable state, so the rebuild is lossless by construction). Default 15m: the
    full-corpus sweep is a cheap in-process pass, and 15m bounds how stale cross-board
    grouping gets between crawls. ``off`` disables the loop (the overlay then only
    moves via a manual ``jobcrawl dedup --rebuild``)."""
    return _str("JOBCRAWLER_DEDUP_REFRESH_INTERVAL", "15m")


def dedup_tiers() -> frozenset[int]:
    """Identity tiers whose keys may form §17 cluster edges (comma-separated ints).
    Default ``0,1,2,3,5,6``: exact canonical URL (0), ATS req-ids (1), normalized
    external apply target (2), external ids within a slug family (3), fuzzy
    company+title (5), LinkedIn/Indeed job ids (6). Tier 5 is the *anchored* fuzzy
    recall — resweep fuses it only when the key names exactly one open direct-board
    posting (the single-open-direct anchor) and the copy is location-compatible, so
    an aggregator duplicate folds under the employer's own row while direct<->direct
    fusion stays structurally impossible. The live between-sweep gates ignore tier 5
    regardless (state._live_key_clause), so it shapes only the materialized clusters.
    Deliberately NOT default: 4 (content-hash — corroboration signal only). Keys for
    disabled tiers are still *written* for audit; they just never join rows. Unknown
    tier numbers are harmless (no such keys exist)."""
    raw = os.getenv("JOBCRAWLER_DEDUP_TIERS")
    if raw is None:
        return frozenset({0, 1, 2, 3, 5, 6})
    return frozenset(int(p.strip()) for p in raw.split(",") if p.strip())


def dedup_match_enabled() -> bool:
    """Consumer switch: §17 verdict inheritance at score-time. On (and master on), a
    cache-missing candidate first looks for a settled cluster sibling and copies its
    verdict — origin-stamped ``cluster:…``, zero LLM spend, never written to the
    match cache, reversible via ``jobcrawl dedup --requeue-inherited``. Off ⇒ every
    candidate scores the old way. Default on."""
    return dedup_enabled() and _bool("JOBCRAWLER_DEDUP_MATCH", True)


def dedup_notify_enabled() -> bool:
    """Consumer switch: the §17 notify-ledger gate. On (and master on), a strong
    match is suppressed when any cluster sibling was already notified — or was
    dismissed or applied to — and every send is recorded as an append-only per-row
    fact in ``notify_ledger``. Off ⇒ notification dedup falls back to the pre-§17
    watermark alone. Default on."""
    return dedup_enabled() and _bool("JOBCRAWLER_DEDUP_NOTIFY", True)


def dedup_ui_enabled() -> bool:
    """Consumer switch: duplicate collapse in the web UI (§17). On (and master on),
    list views show one canonical row per cluster with an "also on N boards"
    affordance, and a cluster any sibling of which was dismissed/applied stays
    collapsed (anti-resurrection); raw rows stay one click away (``?collapse=0``,
    and filters on user-touched rows never collapse). Off ⇒ every board's copy
    lists separately, as before. Default on."""
    return dedup_enabled() and _bool("JOBCRAWLER_DEDUP_UI", True)


def dedup_role_groups_enabled() -> bool:
    """Sweep switch: §17 role groups — bundling one role's location variants (same
    company key + title key, ≥2 distinct locations) for the web layer's "N locations"
    picker. Presentation-only by contract: members are real, separately-applied-to
    postings, so matching/notify/lifecycle never read the table. Off (or master off)
    ⇒ the next sweep clears the table and every variant lists on its own row.
    Default on."""
    return dedup_enabled() and _bool("JOBCRAWLER_DEDUP_ROLE_GROUPS", True)


def dedup_apply_param_denylist() -> frozenset[str]:
    """Query params stripped when normalizing *apply targets* into §17 T2 identity
    keys — a separate, wider list than :func:`tracking_param_denylist`, which feeds
    the §4.6 storage key and must stay conservative (identity keys are disposable;
    storage keys are forever). Identity-bearing params (``gh_jid``, ``ashby_jid``,
    ``for``, ``token``, ``jk``…) are load-bearing there and must never be listed.
    ``JOBCRAWLER_DEDUP_APPLY_PARAM_DENYLIST`` (comma-separated) overrides the curated
    default in :mod:`jobcrawler.runtime.dedup`."""
    raw = os.getenv("JOBCRAWLER_DEDUP_APPLY_PARAM_DENYLIST")
    if raw is None:
        return IDENTITY_APPLY_TRACKING_PARAMS
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


# --- scheduler (§4.9) ------------------------------------------------------


def default_schedule() -> str:
    """Fixed crawl cadence for a site with no ``schedule:`` and adaptive off. Default 2h
    (up from the news-era 30m): job boards move on the order of days, not minutes, so a
    2h base catches a new posting the day it lands while staying polite. Adaptive cadence
    (on by default) overrides this per-site from the observed hit rate."""
    return _str("JOBCRAWLER_DEFAULT_SCHEDULE", "2h")


def concurrency() -> int:
    return _int("JOBCRAWLER_CONCURRENCY", 12)


def home_country() -> str:
    """The operator's home country as an ISO 3166-1 alpha-2 code (default ``BR``).

    Personalizes the "remote anywhere" mobility filter (§16): a role that is
    remotely workable *from here* is as reachable as a worldwide-remote one, so
    that filter's ``remote_anywhere`` arm treats a remote fence covering this
    country — directly or through a macro region (``LATAM``, ``AMERICAS`` …) — the
    same as an unrestricted worldwide remote. Upper-cased; a malformed value
    (not exactly two ASCII letters) falls back to ``BR`` so a typo can never reach
    the arm's inlined SQL literal."""
    raw = (os.getenv("JOBCRAWLER_HOME_COUNTRY") or "BR").strip().upper()
    return raw if len(raw) == 2 and raw.isascii() and raw.isalpha() else "BR"


# --- adaptive cadence (§4.9, the hit-rate controller) ----------------------
# A source flagged ``schedule: adaptive`` learns its own interval from recent
# hit/miss outcomes (see :mod:`jobcrawler.adaptive`). These bound and
# tune that controller; all are host-side, never exposed to the generator (§8).


def _duration_secs(name: str, default: str) -> float:
    """An ``JOBCRAWLER_*`` duration env var as float seconds (falls back on a
    malformed value to the default, so a typo can't crash the daemon at boot)."""
    raw = os.getenv(name)
    if raw is None:
        return parse_duration(default).total_seconds()
    try:
        return parse_duration(raw).total_seconds()
    except ValueError:
        return parse_duration(default).total_seconds()


def min_schedule() -> float:
    """Politeness floor (seconds) for an adaptive interval — the controller never shrinks
    below this no matter how busy a source is. Default 30m (up from the news-era 15m): job
    postings don't churn minute-to-minute, so a half-hour floor is the fastest a board ever
    needs to be hit, and keeps the fleet polite under a busy hit rate."""
    return _duration_secs("JOBCRAWLER_MIN_SCHEDULE", "30m")


def max_schedule() -> float:
    """Staleness ceiling (seconds) for an adaptive interval — the controller never grows
    beyond this no matter how quiet a source is. Default 3d (up from the news-era 1d): a
    job typically stays open for weeks, so even a near-silent board is fresh enough
    checked every three days, and the longer ceiling saves fetches on quiet sources."""
    return _duration_secs("JOBCRAWLER_MAX_SCHEDULE", "3d")


def adaptive_target_n() -> float:
    """Desired checks-per-post for an adaptive source: the controller steers the hit
    rate toward ``1 / target_n``. Default 2 (≈ check twice per post). Clamped ≥ 1 so
    the target hit rate stays in (0, 1]."""
    return max(1.0, _float("JOBCRAWLER_ADAPTIVE_TARGET_N", 2.0))


def bootstrap_budget() -> float:
    """Per-tick time budget (seconds) for the §4.2 cold-start bootstrap pass.

    One generation blocks the single-process §4.9 loop for many minutes, and a
    cold-start roster can queue dozens of crawler-less sites into a single tick —
    unbudgeted, that one tick bootstraps the whole roster back-to-back while every
    already-promoted crawler sits idle, its first crawl (and the match pipeline
    behind it) starved until the wave ends. Cheap assesses (a site in backoff)
    sweep through freely; once the pass exceeds this budget it defers the rest of
    the roster to the next tick — a crawler-less site is re-detected from disk
    every tick, so deferral loses nothing. Default: three tick intervals."""
    return _duration_secs("JOBCRAWLER_BOOTSTRAP_BUDGET", "3m")


def bootstrap_concurrency() -> int:
    """How many crawler generations may run at once (default 16).

    Paces both generation passes: the §4.2 cold-start bootstrap and the §4.8 staleness
    sweep run their assesses in concurrent waves of this size. A fresh roster (or a
    correlated drift wave) otherwise generates one crawler at a time — N back-to-back
    multi-minute LM generations before the fleet is online. Each generation is LM-heavy,
    so this fan-out multiplies the instantaneous token load — back it off if the
    provider starts rate-limiting. ``1`` restores the fully sequential passes. The
    per-pass time budgets and the forced-slug exemption apply across waves exactly as
    before."""
    return max(1, _int("JOBCRAWLER_BOOTSTRAP_CONCURRENCY", 16))


def regen_budget() -> float:
    """Per-sweep time budget (seconds) for the §4.8 periodic staleness sweep.

    ``regen_tick`` assesses every live site; a drifted one regenerates in-band, blocking
    the single-process §4.9 loop for minutes. A correlated drift wave (an ATS HTML change
    hitting a dozen boards at once, or the first post-boot sweep over a cold roster) would
    otherwise run every regeneration back-to-back in one tick, freezing all crawling and
    matching until the wave clears. Cheap assesses (healthy or in backoff) sweep freely;
    once the sweep exceeds this budget the rest defers — but unlike the periodic cadence
    the deferral resumes on the *next* tick, not a full ``JOBCRAWLER_REGEN_CHECK`` interval
    later, so a bounded slice of the backlog clears each tick until it drains. Default:
    three tick intervals."""
    return _duration_secs("JOBCRAWLER_REGEN_BUDGET", "3m")


def recheck_budget() -> float:
    """Per-sweep time budget (seconds) for the §4.9 windowed-source recheck pass.

    ``recheck_tick`` re-fetches up to ``JOBCRAWLER_RECHECK_CAP`` still-open URLs *per*
    windowed site; the per-fetch watchdog bounds each request but nothing bounds the
    aggregate. A fleet of windowed sources therefore runs ``cap × sites`` sequential
    fetches in a single tick, holding the single-process §4.9 loop and starving crawl,
    regen, and matching — the same freeze the §4.8 regen budget already prevents. Once a
    sweep exceeds this budget the remaining URLs defer; because every attempted URL was
    rotated off the head (its ``last_seen`` restamped, #39), the next tick resumes at the
    still-stalest remainder rather than re-checking the same head, and — like regen — an
    incomplete sweep resumes on the *next* tick rather than a full ``JOBCRAWLER_RECHECK_CHECK``
    interval later. ``0`` disables the budget (an unbounded sweep, matching the pre-#9
    behaviour and the watchdog's own ``0``-disables convention). Default: three tick
    intervals."""
    return _duration_secs("JOBCRAWLER_RECHECK_BUDGET", "3m")


def assess_timeout() -> float:
    """Liveness watchdog (seconds) for ONE §4.2/§4.8 generation assess (§4.9).

    The tick awaits each assess in-band, so without a bound a single wedged
    generation — a stuck sandbox subprocess, a hung network read — freezes crawling,
    recheck and matching fleet-wide (observed live: a 50-minute total stall with a
    zombie sandbox child). On expiry the scheduler abandons that assess — the worker
    thread keeps running detached and its eventual store writes still land; it just
    can't hold the loop hostage — and the pass moves on. Generous by default: a
    legitimate best-of-N generation with self-heal rounds runs 10-25 minutes.
    ``0s`` disables the watchdog."""
    return _duration_secs("JOBCRAWLER_ASSESS_TIMEOUT", "30m")


def adaptive_default() -> bool:
    """When true, a site with no explicit ``schedule`` is adaptive rather than using
    the fixed ``default_schedule``. Default true — the fleet self-tunes each source's
    cadence to its hit-rate; pin ``schedule:`` or ``adaptive: false`` on a site to opt
    out."""
    return _bool("JOBCRAWLER_ADAPTIVE_DEFAULT", True)


def adaptive_window() -> int:
    """How many recent crawls the hit-rate controller averages over. Default 8 —
    long enough to smooth single-crawl noise, short enough to track a real shift."""
    return max(1, _int("JOBCRAWLER_ADAPTIVE_WINDOW", 8))


# --- master autonomy switch (§4.8/§4.11) -----------------------------------


def autonomy() -> bool:
    """Master kill-switch over every self-modifying autonomous action (default on).

    The single operator brake on the daemon's self-modification: the §4.8 periodic
    regen sweep and event-driven self-heal (including auto-promotion), and the §4.11
    out-of-band re-optimization dispatch (including auto-adoption). ``off`` freezes all
    of them at once — the daemon still crawls, ingests, and records run/health signals
    (so it keeps gathering the data the loop uses once re-enabled), but never rewrites a
    crawler or swaps the generator on its own.

    It deliberately does *not* touch crawl scheduling (adaptive cadence is timing, not
    code, and bounded by the politeness band) or any *operator-initiated* command —
    ``promote``, ``optimize --adopt``, ``rollback`` and ``rollback-generator`` stay
    available, because the brake is on the daemon's initiative, not the operator's. Read
    once at scheduler start like the other §4.9 knobs; flip it and restart to apply (the
    loop is restart-idempotent, so a restart loses no state)."""
    return _bool("JOBCRAWLER_AUTONOMY", True)


def regen_check() -> str:
    return _str("JOBCRAWLER_REGEN_CHECK", "24h")


def heal_cooldown() -> str:
    """Minimum spacing between *event-driven* self-heals of one site (§4.8).

    A crawl that comes back blacked-out (zero items) or with collapsed body quality
    triggers an immediate staleness assessment from the run loop, instead of waiting
    for the next periodic ``JOBCRAWLER_REGEN_CHECK`` sweep. This cooldown rate-limits
    that fast path per-site so a quick adaptive cadence (or an auto-promote that keeps
    re-drifting) can't re-run the generator every crawl. ``off`` disables event-driven
    healing entirely, leaving only the periodic sweep. Default 6h."""
    return _str("JOBCRAWLER_HEAL_COOLDOWN", "6h")


def description_quality_floor() -> float:
    """Fraction of complete-description positions below which a crawl is a partial-drift
    signal.

    ``description_quality`` is the share of fetched positions whose description cleared
    the completeness bar (§4.6); it sags when a board starts serving title-only stubs or
    truncated postings even while position counts stay healthy. A crawl at or above this
    floor is fine; below it (with positions found) is a drift candidate that triggers a
    §4.8 staleness assessment. Host-side only — never surfaced to the generator (§8).
    Default 0.5. Clamped to (0, 1]; an out-of-range value falls back to the default."""
    value = _float("JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR", 0.5)
    return value if 0.0 < value <= 1.0 else 0.5


def genfail_backoff() -> float:
    """Base delay (seconds) before a generation-failed site is re-tried (§4.8).

    A site whose regeneration exhausted its budget is held, not abandoned: after this
    backoff it is re-tried with the *current* generator (which the offline §4.11 loop
    keeps improving). The delay grows geometrically per consecutive failure (×2 each)
    up to ``genfail_backoff_cap``, so a chronically broken site backs off without ever
    giving up. Default 1h."""
    return _duration_secs("JOBCRAWLER_GENFAIL_BACKOFF", "1h")


def genfail_backoff_cap() -> float:
    """Ceiling (seconds) for the generation-failed retry backoff. Default 5d."""
    return _duration_secs("JOBCRAWLER_GENFAIL_BACKOFF_CAP", "5d")


def genfail_max_attempts() -> int:
    """Consecutive generation failures before a site stops being auto-re-tried (§4.8).

    The geometric backoff alone never gives up: at the cap it re-generates a chronically
    dead board (a retired URL, an un-crawlable rewrite) every ``genfail_backoff_cap``
    forever, burning generation budget on a board that will never come back on its own.
    After this many consecutive failures the site is *parked* — held ``generation_failed``
    and no longer re-generated — so the spend is bounded. It stays surfaced (not silent),
    and un-parks the moment it heals on its own or an operator regenerates it by hand.
    ``0`` disables the cap (retry forever, the historical behavior). Default 5 — with the
    1h base backoff that is roughly fifteen hours of escalating retries (1+2+4+8h),
    enough to ride out an overnight outage before giving up; a genuinely hopeless board
    (a bot wall, a retired URL) stops burning ~85-call generation attempts within a
    day."""
    return _int("JOBCRAWLER_GENFAIL_MAX_ATTEMPTS", 5)


def reopt_check() -> str:
    """How often the scheduler checks whether to dispatch the offline §4.11 GEPA
    self-improvement job (out-of-band; ``.compile()`` never runs in-band). Default 24h;
    ``off`` disables it. A check only *dispatches* when the readiness gates (trainset
    growth + pool) pass and no compile is already running, so a daily check is not a
    daily compile — and an unconfigured optimizer model just no-ops the dispatch."""
    return _str("JOBCRAWLER_REOPT_CHECK", "24h")


def optimize_lock_path() -> str:
    """Single-flight lockfile for the offline §4.11 optimize job (held for its run).

    The GEPA compile is multi-hour; ``optimize_job`` holds an advisory lock on this
    file for the whole run so a reopt tick that fires while a previous compile is
    still going is a no-op rather than a pile-up of concurrent runs fighting over the
    same trainset and generator dir. CWD-relative by default, like the trainset."""
    return _str("JOBCRAWLER_OPTIMIZE_LOCK", "optimize.lock")


def optimize_log_path() -> str:
    """Where the detached out-of-band optimize subprocess appends its stdout/stderr.

    The §4.11 hand-off runs ``jobcrawl optimize`` detached (it outlives the daemon),
    so its progress and the final PROMOTED / not-promoted line land here rather than
    on the daemon's console. CWD-relative by default, like the trainset."""
    return _str("JOBCRAWLER_OPTIMIZE_LOG", "optimize.log")


def trainset_stable_runs() -> int:
    return _int("JOBCRAWLER_TRAINSET_STABLE_RUNS", 3)


def promotion_eval_passes() -> int:
    """Independent held-out eval passes averaged before a §4.11 promotion decision.

    The generator is stochastic, so one pass over a small frozen held-out is noisy
    (~a few points of cross-run std) — enough to promote a candidate on sampling
    luck. Averaging a few passes makes ``should_promote`` compare stable estimates.
    Default 3 (promotion rigor); set to 1 to restore single-pass (cheaper, noisier).
    """
    return max(1, _int("JOBCRAWLER_PROMOTION_EVAL_PASSES", 3))


def trainset_path() -> str:
    """The §4.11 trainset records store (one JSONL row per accepted generation)."""
    return _str("JOBCRAWLER_TRAINSET_PATH", "trainset.jsonl")


def generator_dir() -> str:
    """Where `jobcrawl optimize` writes versioned, promoted ``generator-N.json``
    artifacts; the operator points ``JOBCRAWLER_GENERATOR_PATH`` at one to deploy."""
    return _str("JOBCRAWLER_GENERATOR_DIR", "generators")


def generator_path() -> str | None:
    """The single §4.11 config pointer to the active ``generator.json``; flipping it
    to a prior artifact is the rollback. Unset ⇒ boot from the baseline generator."""
    return _opt("JOBCRAWLER_GENERATOR_PATH")


def adopt_probation() -> timedelta | None:
    """Probation window after a generator adoption before the §4.11 rollback watcher
    judges it (``off`` disables the watcher entirely). Default 48h.

    "Full auto, gated by proof" adopts a generator the instant it clears the frozen
    held-out margin — but that held-out is a proxy, so a generator can pass it and
    still regress on the live fleet, where it drives every §4.8 self-heal. This is the
    window the watcher waits before comparing post-adoption generation failures against
    the equal window before adoption: long enough for the new generator to actually be
    exercised on a few regenerations, short enough to revert a bad one promptly. A
    malformed value falls back to the default so a typo can't wedge the watcher; ``off``
    (or empty) returns None, leaving auto-adoption protected only by the margin gate.
    Host-side (§8) — the watcher never surfaces anything to the generator/RLM."""
    raw = _str("JOBCRAWLER_ADOPT_PROBATION", "48h")
    if raw.strip().lower() in {"off", ""}:
        return None
    try:
        return parse_duration(raw)
    except ValueError:
        return parse_duration("48h")


def adopt_regression_delta() -> int:
    """How many *more* sites must newly enter ``generation_failed`` in the window after
    a generator adoption than in the equal window before it for the §4.11 watcher to
    call it a regression and roll the pointer back. Default 2.

    An adopted generator only affects *new* regenerations, so a regression shows up as
    post-adoption self-heals failing to produce a gate-passing crawler more often than
    the prior generator did. Comparing against the pre-adoption baseline (not an
    absolute count) normalizes for a fleet's ambient flakiness; the delta is the noise
    margin on top — one extra failure can be chance, a sustained increase of two
    distinct sites is the rollback signal. Floored at 1 so a 0/negative value can't make
    every adoption trip. Host-side (§8)."""
    return max(1, _int("JOBCRAWLER_ADOPT_REGRESSION_DELTA", 2))


# --- Cloudflare / solver (§4.10) -------------------------------------------


def solver() -> str:
    """Challenge-solver backend (§4.10). Defaults to ``none`` (plain httpx): most
    news sites need no solver, and the in-process stealth backends aren't built, so
    a non-``none`` default would make ``make_solver`` raise on boot. ``flaresolverr``
    (+ ``FLARESOLVERR_URL``) is the opt-in backend."""
    return _str("JOBCRAWLER_SOLVER", "none")


def flaresolverr_url() -> str | None:
    return _opt("FLARESOLVERR_URL")


def clearance_ttl() -> str:
    return _str("JOBCRAWLER_CLEARANCE_TTL", "30m")


def egress_proxy() -> str | None:
    return _opt("JOBCRAWLER_EGRESS_PROXY")


def egress_enforce() -> str:
    """Tri-state §8/#19b OS-enforced-egress policy for the sandbox child (default ``auto``).

    The env scrub (#19a) removes the loot; this governs the *exfil channel*. The child
    always fetches through a per-crawl allowlisting loopback proxy (the data plane), but
    whether an OS mechanism additionally forbids the child from opening any *other* socket
    is a detect-and-degrade ladder:

      * ``auto`` — the default: use the strongest OS rung actually available on the box
        (seccomp connect-broker / netns), and if none is, degrade to the proxy-only path
        and log the honest posture. Never refuses a crawl for a missing kernel feature.
      * ``require`` — refuse the crawl (SandboxError-shaped, the site is skipped) unless a
        real OS enforcement rung is armed. For high-assurance operators who would rather
        skip a site than crawl it uncontained.
      * ``off`` — the mechanism is bypassed (legacy direct client); still logged.

    Parsed like :func:`review_gate`: unset → ``auto``; ``on``/``1``/``true``/``yes`` →
    ``require``; ``off``/``0``/``false``/``no`` → ``off``; an unknown value → ``auto`` (the
    safe, non-crawl-breaking default). :func:`egress_proxy` keeps its meaning — the
    operator UPSTREAM proxy the loopback allowlist proxy chains through.
    """
    raw = os.getenv("JOBCRAWLER_EGRESS_ENFORCE")
    if raw is None:
        return "auto"
    value = raw.strip().lower()
    if value in {"auto", "require", "off"}:
        return value
    if value in {"on", "1", "true", "yes"}:
        return "require"
    if value in {"0", "false", "no"}:
        return "off"
    return "auto"


def egress_resolve_timeout() -> float:
    """``getaddrinfo`` budget (seconds) for the egress proxy's resolve-and-pin step (#19b).

    Bounds a single DNS lookup in the trusted-parent proxy so a slow resolver can't wedge
    a child's fetch; kept tight since the child does zero DNS of its own."""
    return _float("JOBCRAWLER_EGRESS_RESOLVE_TIMEOUT", 5.0)


# --- review (§8) -----------------------------------------------------------


def review_gate() -> str:
    """Tri-state §4.8 self-heal & §4.2 cold-start delivery policy (default ``auto``):

      * ``auto`` — auto-promote (the default): back up the current live crawler to
        ``crawlers/_history/<slug>/`` then write the new one live, so the site
        self-heals — and a brand-new site bootstraps — unattended. Safety is the §6
        gates plus versioned rollback (``jobcrawl rollback``), not a human (the §4.8
        "full-auto, gated by proof").
      * ``on`` — stage a crawler to ``crawlers/_pending/<slug>.py`` and mark the site
        pending-review; an operator runs ``jobcrawl promote``. A human approves every
        live change (most conservative).
      * ``off`` — write live with no backup (operator-trusted; no rollback copy).

    The legacy boolean values are still accepted: truthy ⇒ ``on`` (the prior staging
    behavior), falsy ⇒ ``off`` (the prior go-live), so a deployment that set those keeps
    its behavior. An *unset* gate now defaults to ``auto`` (autonomous); an unrecognized
    value still falls back to ``on`` (the safe, human-gated path).
    """
    raw = os.getenv("JOBCRAWLER_REVIEW_GATE")
    if raw is None:
        return "auto"
    value = raw.strip().lower()
    if value in {"on", "auto", "off"}:
        return value
    if value in {"1", "true", "yes"}:
        return "on"
    if value in {"0", "false", "no"}:
        return "off"
    return "on"


# --- observability (§4.9) --------------------------------------------------


def log_level() -> str:
    """Root log level for the daemon/CLI (``JOBCRAWLER_LOG_LEVEL``; default INFO).

    Upper-cased so a lowercase value still names a level ``logging.setLevel`` accepts.
    """
    return _str("JOBCRAWLER_LOG_LEVEL", "INFO").upper()


def notify_webhook() -> str | None:
    """Optional webhook for operator alerts on autonomous actions (None = log only).

    When ``JOBCRAWLER_NOTIFY_WEBHOOK`` is an ``http(s)`` URL, every self-modifying
    action (§4.8 heal/promote, §4.11 adopt, generator rollback) POSTs a small JSON
    payload there in addition to the log line, so an operator gets pushed an alert
    when the daemon changes its own code without having to tail logs. Best-effort and
    fire-and-forget: a webhook that is slow or down never blocks or aborts the action
    (see :mod:`jobcrawler.notify`). Unset → notifications are logged only."""
    return _opt("JOBCRAWLER_NOTIFY_WEBHOOK")


def notify_timeout() -> float:
    """Per-POST timeout (seconds) for the notify webhook (``JOBCRAWLER_NOTIFY_TIMEOUT``).

    Bounds how long a single best-effort delivery attempt waits before giving up, so a
    hung receiver can't pile up background threads. Default 10s."""
    return _float("JOBCRAWLER_NOTIFY_TIMEOUT", 10.0)


# --- web UI (jobcrawl serve, §16) ------------------------------------------


def serve_host() -> str:
    """Bind address for the position-tracking web UI (``JOBCRAWLER_SERVE_HOST``).

    Loopback (``127.0.0.1``) by default: the UI reads and writes personal application data
    (who you've applied to, private notes) over the same §4.7 store the daemon owns, so it
    must not be exposed on the network out of the box. Only set ``0.0.0.0`` when the UI sits
    behind a trusted reverse proxy that adds authentication."""
    return _str("JOBCRAWLER_SERVE_HOST", "127.0.0.1")


def serve_port() -> int:
    """TCP port for the position-tracking web UI (``JOBCRAWLER_SERVE_PORT``).

    Default 8765 — deliberately clear of the common dev-server ports (3000/5173/8000/8080/
    8888) so ``jobcrawl serve`` doesn't collide with whatever else you're running."""
    return _int("JOBCRAWLER_SERVE_PORT", 8765)
