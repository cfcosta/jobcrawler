"""Runtime configuration for bizup-autocrawler, read from the environment (§11).

Every env knob the service reads lives here, so the full configuration surface
is visible in one place. All thresholds are host-side constants — never exposed
to the generator/RLM (the §8 hidden-scorer invariant). Getters are thin
wrappers over a few typed parsers.
"""

from __future__ import annotations

import os

from bizup_autocrawler.urls import DEFAULT_TRACKING_PARAMS

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


def model() -> str:
    """RLM root planner/extractor — a strong, code-capable model."""
    return _str("AUTOCRAWLER_MODEL", "openrouter/qwen/qwen3.7-plus")


def sub_model() -> str | None:
    """RLM ``sub_lm``. Required: unset ⇒ every ``llm_query`` bills at root rates,
    so callers must error rather than silently fall back (§4.2)."""
    return _opt("AUTOCRAWLER_SUB_MODEL")


def reflection_model() -> str | None:
    """GEPA ``reflection_lm`` (strong, ideally a different family); §4.11/M5."""
    return _opt("AUTOCRAWLER_REFLECTION_MODEL")


# --- generation caps (§4.2) ------------------------------------------------


def max_iterations() -> int:
    return _int("AUTOCRAWLER_MAX_ITERATIONS", 20)


def max_llm_calls() -> int:
    return _int("AUTOCRAWLER_MAX_LLM_CALLS", 40)


def max_subcalls() -> int:
    return _int("AUTOCRAWLER_MAX_SUBCALLS", 60)


def fanout_spend_cap() -> str:
    return _str("AUTOCRAWLER_FANOUT_SPEND_CAP", "$8/site")


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
    return _str("AUTOCRAWLER_SOLVER", "stealth")


def flaresolverr_url() -> str | None:
    return _opt("FLARESOLVERR_URL")


def clearance_ttl() -> str:
    return _str("AUTOCRAWLER_CLEARANCE_TTL", "30m")


def egress_proxy() -> str | None:
    return _opt("AUTOCRAWLER_EGRESS_PROXY")


# --- review (§8) -----------------------------------------------------------


def review_gate() -> bool:
    return _bool("AUTOCRAWLER_REVIEW_GATE", True)
