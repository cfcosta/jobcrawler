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
from jobcrawler.urls import DEFAULT_TRACKING_PARAMS

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
    """RLM root planner/extractor — a strong, code-capable model. Required: there is
    no safe default (a placeholder slug fails opaque at the provider), so generation
    call sites use :func:`require_model`, never a fabricated default."""
    return _opt("JOBCRAWLER_MODEL")


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
    """RLM ``sub_lm``. Required: unset ⇒ every ``llm_query`` bills at root rates,
    so callers must error rather than silently fall back (§4.2)."""
    return _opt("JOBCRAWLER_SUB_MODEL")


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
    """The host-side §4.7 match LM slug (``JOBCRAWLER_MATCH_MODEL``). Optional: unset,
    matching falls back to the cheap ``sub_lm`` via :func:`require_match_model` rather
    than the root planner — so a deployment need only name a dedicated match model when
    it wants one distinct from ``sub_lm``."""
    return _opt("JOBCRAWLER_MATCH_MODEL")


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


def match_daily_cap() -> int:
    """Hard per-UTC-day ceiling on the number of jobs sent to the match LM — the
    token-budget backstop (§4.7/§8). Only *LLM-scored* jobs count: a cache hit (a job
    whose ``content_hash`` + ``profile_hash`` was already scored) is FREE and never
    charged against it. Jobs past the cap are ``set_match_pending``, NEVER dropped — a
    later run or the recheck loop drains them within budget. Default 400: bounds a bad
    crawl or a fresh-profile rematch storm from blowing the daily spend. Raise it only
    with the token bill in view."""
    return _int("JOBCRAWLER_MATCH_DAILY_CAP", 400)


def match_rate() -> str:
    """Governor pacing for the §4.7 match LM calls (``requests/interval``, e.g. the
    default ``10/m``). Smooths the batched match calls so a big new-position sweep can't
    burst the provider into rate-limit errors; it paces *calls*, orthogonal to the daily
    token cap which bounds *volume*. Same governor grammar as the crawl rate limit."""
    return _str("JOBCRAWLER_MATCH_RATE", "10/m")


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


# --- generation caps (§4.2) ------------------------------------------------


def max_iterations() -> int:
    return _int("JOBCRAWLER_MAX_ITERATIONS", 20)


def max_llm_calls() -> int:
    return _int("JOBCRAWLER_MAX_LLM_CALLS", 40)


def max_subcalls() -> int:
    return _int("JOBCRAWLER_MAX_SUBCALLS", 60)


def fanout_spend_cap() -> str:
    return _str("JOBCRAWLER_FANOUT_SPEND_CAP", "$8/site")


def fanout_spend_cap_usd() -> float:
    """The §4.2 aggregate fan-out spend ceiling as a float USD/site.

    Parses the human-friendly ``$8/site`` form (and a bare ``8`` / ``8.0``) into the
    dollar budget the selection fan-out is bounded by. Size it against the p95 tail,
    not the mean (§4.2).
    """
    raw = fanout_spend_cap().strip().lstrip("$").split("/")[0].strip()
    return float(raw)


def fanout_n() -> int:
    """Number of candidates the §6 best-of-N generates (default N≈4–8)."""
    return _int("JOBCRAWLER_FANOUT_N", 4)


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
    return _str("JOBCRAWLER_RATE_LIMIT", "1/s/host")


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


def recheck_cap() -> int:
    """Max standing positions the §4.9 recheck loop re-fetches per pass. Bounds the extra
    fetch (and solver/politeness) budget the loop spends on already-known rows so a fleet
    with thousands of open positions doesn't starve fresh crawls; the oldest-checked rows
    go first, so all rows are covered over successive passes. Default 150."""
    return _int("JOBCRAWLER_RECHECK_CAP", 150)


def match_drain_interval() -> str:
    """How often the daemon's §4.7 match-drain tick runs: reload ``PROFILE.md`` (a hash
    change requeues the open backlog for rescoring) and re-present parked pending rows
    to the matcher straight from the store (their descriptions are persisted, so no
    crawl round-trip). Default 10m — fast enough that a profile edit feels live, cheap
    because an empty backlog is a no-op and every LLM call stays under the daily cap.
    ``off`` disables the tick (pending then drains only via re-crawls, the old way)."""
    return _str("JOBCRAWLER_MATCH_DRAIN_INTERVAL", "10m")


def match_drain_cap() -> int:
    """Max parked rows one match-drain tick re-presents to the matcher. Bounds a single
    tick's LLM exposure after a profile edit requeues thousands of rows; successive
    ticks (FIFO) work through the rest, and the §8 daily cap still bounds total spend.
    Default 200."""
    return _int("JOBCRAWLER_MATCH_DRAIN_CAP", 200)


# --- scheduler (§4.9) ------------------------------------------------------


def default_schedule() -> str:
    """Fixed crawl cadence for a site with no ``schedule:`` and adaptive off. Default 4h
    (up from the news-era 30m): job boards move on the order of days, not minutes, so a
    4h base catches a new posting the day it lands while staying polite. Adaptive cadence
    (on by default) overrides this per-site from the observed hit rate."""
    return _str("JOBCRAWLER_DEFAULT_SCHEDULE", "4h")


def concurrency() -> int:
    return _int("JOBCRAWLER_CONCURRENCY", 4)


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
    below this no matter how busy a source is. Default 1h (up from the news-era 15m): job
    postings don't churn minute-to-minute, so an hourly floor is the fastest a board ever
    needs to be hit, and keeps the fleet polite under a busy hit rate."""
    return _duration_secs("JOBCRAWLER_MIN_SCHEDULE", "1h")


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
    every tick, so deferral loses nothing. Default: one tick interval."""
    return _duration_secs("JOBCRAWLER_BOOTSTRAP_BUDGET", "60s")


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
    giving up. Default 6h."""
    return _duration_secs("JOBCRAWLER_GENFAIL_BACKOFF", "6h")


def genfail_backoff_cap() -> float:
    """Ceiling (seconds) for the generation-failed retry backoff. Default 7d."""
    return _duration_secs("JOBCRAWLER_GENFAIL_BACKOFF_CAP", "7d")


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
