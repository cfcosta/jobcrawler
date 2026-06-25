"""Runtime configuration for bizup-autocrawler, read from the environment (§11).

Every env knob the service reads lives here, so the full configuration surface
is visible in one place. All thresholds are host-side constants — never exposed
to the generator/RLM (the §8 hidden-scorer invariant). Getters are thin
wrappers over a few typed parsers.
"""

from __future__ import annotations

import os
from pathlib import Path

from bizup_autocrawler.urls import DEFAULT_TRACKING_PARAMS

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
    return _opt("AUTOCRAWLER_MODEL")


def require_model() -> str:
    """The root planner model, erroring if unset (§4.2/§11).

    ``AUTOCRAWLER_MODEL`` names the provider model slug for ``dspy.LM``; there is no
    sensible default (the old ``openrouter/qwen/qwen3.7-plus`` was a non-existent
    slug that failed deep in litellm), so the generation/optimize path errors loud
    and early rather than surfacing an opaque provider 404 mid-run.
    """
    value = model()
    if value is None:
        raise RuntimeError(
            "AUTOCRAWLER_MODEL is required (§4.2): the RLM root planner needs a real "
            "provider model slug (e.g. openrouter/<vendor>/<model>). Set it to a "
            "strong, code-capable model."
        )
    return value


def sub_model() -> str | None:
    """RLM ``sub_lm``. Required: unset ⇒ every ``llm_query`` bills at root rates,
    so callers must error rather than silently fall back (§4.2)."""
    return _opt("AUTOCRAWLER_SUB_MODEL")


def require_sub_model() -> str:
    """The ``sub_lm`` model, erroring if unset (§4.2/§11).

    An unset ``sub_lm`` silently falls back to the *root* planner LM inside
    ``dspy.RLM``, billing every in-sandbox ``llm_query`` at planner rates. The spec
    makes ``AUTOCRAWLER_SUB_MODEL`` **required** precisely so this fails loud instead
    of quietly overspending — generation call sites must use this, never the
    ``sub_model() or model()`` fallback it replaces.
    """
    value = sub_model()
    if value is None:
        raise RuntimeError(
            "AUTOCRAWLER_SUB_MODEL is required (§4.2): unset, every in-sandbox "
            "llm_query silently bills at the root planner's rate. Set it to a cheap, "
            "code-capable model (e.g. a small/instruct tier)."
        )
    return value


def reflection_model() -> str | None:
    """GEPA ``reflection_lm`` (strong, ideally a different family); §4.11/M5."""
    return _opt("AUTOCRAWLER_REFLECTION_MODEL")


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
            "AUTOCRAWLER_REFLECTION_MODEL is required for the GEPA optimizer rung "
            "(§4.11): GEPA mutates the strategy prompt by reflecting on candidate "
            "feedback with this LM. Set it to a strong model, ideally a different "
            "family than AUTOCRAWLER_MODEL."
        )
    return value


# --- generation caps (§4.2) ------------------------------------------------


def max_iterations() -> int:
    return _int("AUTOCRAWLER_MAX_ITERATIONS", 20)


def max_llm_calls() -> int:
    return _int("AUTOCRAWLER_MAX_LLM_CALLS", 40)


def max_subcalls() -> int:
    return _int("AUTOCRAWLER_MAX_SUBCALLS", 60)


def fanout_spend_cap() -> str:
    return _str("AUTOCRAWLER_FANOUT_SPEND_CAP", "$8/site")


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
    return _int("AUTOCRAWLER_FANOUT_N", 4)


def max_tokens() -> int:
    """LM output-token cap. Generous by default so a reasoning model has room for
    both its thinking and the answer on the long RLM action prompt — too low and
    a reasoning model spends the whole budget thinking and returns empty content.
    """
    return _int("AUTOCRAWLER_MAX_TOKENS", 16000)


def corpus_homepage_chars() -> int:
    """Cap on homepage HTML kept in the §9 snapshot. Raw pages run to 100s of KB;
    unbounded, the corpus blows past the model's context and a reasoning LM burns
    its whole token budget (empty output). Link discovery still uses the full page."""
    return _int("AUTOCRAWLER_CORPUS_HOMEPAGE_CHARS", 60000)


def corpus_article_chars() -> int:
    """Cap on each article sample kept in the snapshot (enough to learn the body
    selector; a content-heavy page is otherwise 100s of KB of cruft)."""
    return _int("AUTOCRAWLER_CORPUS_ARTICLE_CHARS", 20000)


# --- ingest / news (§4.7) --------------------------------------------------


def news_api_url() -> str:
    return _str("NEWS_API_URL", "http://localhost:8000")


def sites_path() -> str:
    return _str("AUTOCRAWLER_SITES", "sites.yaml")


def crawlers_dir() -> str:
    """Directory of committed per-site crawlers — ``crawlers/<slug>.py`` plus the
    §14.2 ``_pending`` staging area. Read by crawl/probe, written by
    generate/promote and the §4.8 self-heal.

    Defaults to the in-package ``crawlers/`` dir, where the editable dev install
    resolves them and the baked example crawler lives. A production deploy must point
    ``AUTOCRAWLER_CRAWLERS_DIR`` at a writable, persistent path (e.g. a mounted
    ``/data/crawlers``): the in-package dir is read-only on a native Nix-store install
    (so generate/promote/self-heal can't write) and ephemeral in a container layer (so
    generated crawlers vanish on restart). The crawl loader reads the script *text*
    (never imports the package), so an out-of-package dir works for the whole
    crawl/probe/generate/promote surface.
    """
    return _str("AUTOCRAWLER_CRAWLERS_DIR", str(_PKG_DIR / "crawlers"))


def first_crawl_cap() -> int:
    return _int("AUTOCRAWLER_FIRST_CRAWL_CAP", 30)


def ingest_rate() -> str:
    return _str("AUTOCRAWLER_INGEST_RATE", "2/s")


def ingest_timeout() -> float:
    """POST /news client timeout. Each ingest drives server-side LLM summarization +
    tagging, which on a long body runs to tens of seconds — too tight a timeout
    spuriously trips the TERMINAL path (the row often lands server-side anyway), so
    default generously. Lower it only if the news API is fast."""
    return _float("AUTOCRAWLER_INGEST_TIMEOUT", 120.0)


# Substrings (case-insensitive) that mark a body as a paywall teaser, not a
# complete article. A teaser (standfirst + one paragraph + a "subscribe" pitch)
# can exceed min_body_chars, so the length floor alone misses it (§4.6). Confirmed
# on valor.globo.com; extend per deployment with AUTOCRAWLER_PAYWALL_MARKERS.
_DEFAULT_PAYWALL_MARKERS = (
    "exclusiva para assinantes",
    "para ter acesso completo",
)


def paywall_markers() -> tuple[str, ...]:
    """Paywall-teaser markers dropped at ingest even above the length floor (§4.6).
    Override with ``AUTOCRAWLER_PAYWALL_MARKERS`` (``|``-separated; empty disables)."""
    raw = os.getenv("AUTOCRAWLER_PAYWALL_MARKERS")
    if raw is None:
        return _DEFAULT_PAYWALL_MARKERS
    return tuple(m.strip() for m in raw.split("|") if m.strip())


# --- crawl / politeness (§4.5) ---------------------------------------------


def state_url() -> str:
    return _str("AUTOCRAWLER_STATE_URL", "sqlite:///autocrawler.db")


def user_agent() -> str:
    return _str("AUTOCRAWLER_USER_AGENT", "bizup-autocrawler/0.1 (+news aggregator)")


def rate_limit() -> str:
    return _str("AUTOCRAWLER_RATE_LIMIT", "1/s/host")


def max_pages() -> int:
    return _int("AUTOCRAWLER_MAX_PAGES", 200)


def fetch_timeout() -> float:
    """Per-request HTTP timeout for a production crawl's fetches (§4.5).

    Bounds a single hung fetch so one stuck socket can't eat the whole subprocess
    wall-clock budget (:func:`crawl_timeout`); kept well under it. The validation
    sandbox keeps its own tight default — this is the live crawl/robots client."""
    return _float("AUTOCRAWLER_FETCH_TIMEOUT", 20.0)


def fetch_retries() -> int:
    """Extra attempts after the first for a *transient* fetch failure (§4.5) — a
    429/503/502/504 or a dropped connection. ``0`` disables retry. A Cloudflare
    challenge is never counted here (it escalates to the solver, not a blind retry)."""
    return _int("AUTOCRAWLER_FETCH_RETRIES", 2)


def fetch_backoff() -> float:
    """Base seconds for the exponential backoff (base·2ⁿ) between transient retries."""
    return _float("AUTOCRAWLER_FETCH_BACKOFF", 0.5)


def fetch_retry_cap() -> float:
    """Ceiling on any single retry wait (§4.5), in seconds. Also the longest server
    ``Retry-After`` we'll honor: a larger one is refused rather than parking the crawl
    budget, and the response is surfaced as an error instead."""
    return _float("AUTOCRAWLER_FETCH_RETRY_CAP", 30.0)


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
    return _float("AUTOCRAWLER_CRAWL_TIMEOUT", 600.0)


def crawl_cpu_seconds() -> float:
    """RLIMIT_CPU (CPU-seconds) for one committed crawl's subprocess (§4.4).

    Decoupled from :func:`crawl_timeout`: a legitimate crawl is I/O-bound and burns
    little CPU even over a long wall-clock, so this stays tight to kill a compute
    spin (an infinite ``while`` loop blows past any finite CPU budget) without
    mistaking a slow-but-paced crawl for a runaway."""
    return _float("AUTOCRAWLER_CRAWL_CPU_SECONDS", 120.0)


def crawl_mem_mb() -> int:
    """RLIMIT_AS (MB) for one committed crawl's subprocess — caps a memory blowup
    so one bad crawler can't OOM the daemon. Matches the validation sandbox."""
    return _int("AUTOCRAWLER_CRAWL_MEM_MB", 1024)


def tracking_param_denylist() -> frozenset[str]:
    raw = os.getenv("AUTOCRAWLER_TRACKING_PARAM_DENYLIST")
    if raw is None:
        return DEFAULT_TRACKING_PARAMS
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


# --- gate thresholds (§6; M1 placeholders, calibrate per §14 Q8) ------------


def min_items() -> int:
    return _int("AUTOCRAWLER_MIN_ITEMS", 5)


def min_body_chars() -> int:
    return _int("AUTOCRAWLER_MIN_BODY_CHARS", 400)


def min_body_quality() -> float:
    """Fraction of sampled bodies that must clear ``min_body_chars`` for the §6
    completeness gate to pass. < 1.0 tolerates a minority of genuinely-short posts
    (a news brief), which the runtime completeness filter drops at ingest anyway —
    so generation isn't stricter than runtime. A mostly-summary crawler still fails."""
    return _float("AUTOCRAWLER_MIN_BODY_QUALITY", 0.6)


def stability_jaccard() -> float:
    return _float("AUTOCRAWLER_STABILITY_JACCARD", 0.6)


def enum_floor_ratio() -> float:
    return _float("AUTOCRAWLER_ENUM_FLOOR_RATIO", 0.5)


# --- scheduler (§4.9) ------------------------------------------------------


def default_schedule() -> str:
    return _str("AUTOCRAWLER_DEFAULT_SCHEDULE", "30m")


def concurrency() -> int:
    return _int("AUTOCRAWLER_CONCURRENCY", 4)


def regen_check() -> str:
    return _str("AUTOCRAWLER_REGEN_CHECK", "24h")


def reopt_check() -> str:
    return _str("AUTOCRAWLER_REOPT_CHECK", "off")


def trainset_stable_runs() -> int:
    return _int("AUTOCRAWLER_TRAINSET_STABLE_RUNS", 3)


def promotion_eval_passes() -> int:
    """Independent held-out eval passes averaged before a §4.11 promotion decision.

    The generator is stochastic, so one pass over a small frozen held-out is noisy
    (~a few points of cross-run std) — enough to promote a candidate on sampling
    luck. Averaging a few passes makes ``should_promote`` compare stable estimates.
    Default 3 (promotion rigor); set to 1 to restore single-pass (cheaper, noisier).
    """
    return max(1, _int("AUTOCRAWLER_PROMOTION_EVAL_PASSES", 3))


def trainset_path() -> str:
    """The §4.11 trainset records store (one JSONL row per accepted generation)."""
    return _str("AUTOCRAWLER_TRAINSET_PATH", "trainset.jsonl")


def generator_dir() -> str:
    """Where `autocrawl optimize` writes versioned, promoted ``generator-N.json``
    artifacts; the operator points ``AUTOCRAWLER_GENERATOR_PATH`` at one to deploy."""
    return _str("AUTOCRAWLER_GENERATOR_DIR", "generators")


def generator_path() -> str | None:
    """The single §4.11 config pointer to the active ``generator.json``; flipping it
    to a prior artifact is the rollback. Unset ⇒ boot from the baseline generator."""
    return _opt("AUTOCRAWLER_GENERATOR_PATH")


# --- Cloudflare / solver (§4.10) -------------------------------------------


def solver() -> str:
    """Challenge-solver backend (§4.10). Defaults to ``none`` (plain httpx): most
    news sites need no solver, and the in-process stealth backends aren't built, so
    a non-``none`` default would make ``make_solver`` raise on boot. ``flaresolverr``
    (+ ``FLARESOLVERR_URL``) is the opt-in backend."""
    return _str("AUTOCRAWLER_SOLVER", "none")


def flaresolverr_url() -> str | None:
    return _opt("FLARESOLVERR_URL")


def clearance_ttl() -> str:
    return _str("AUTOCRAWLER_CLEARANCE_TTL", "30m")


def egress_proxy() -> str | None:
    return _opt("AUTOCRAWLER_EGRESS_PROXY")


# --- review (§8) -----------------------------------------------------------


def review_gate() -> bool:
    return _bool("AUTOCRAWLER_REVIEW_GATE", True)


# --- observability (§4.9) --------------------------------------------------


def log_level() -> str:
    """Root log level for the daemon/CLI (``AUTOCRAWLER_LOG_LEVEL``; default INFO).

    Upper-cased so a lowercase value still names a level ``logging.setLevel`` accepts.
    """
    return _str("AUTOCRAWLER_LOG_LEVEL", "INFO").upper()
