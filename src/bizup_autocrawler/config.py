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


# --- ingest / news (§4.7) --------------------------------------------------


def news_api_url() -> str:
    return _str("NEWS_API_URL", "http://localhost:8000")


def first_crawl_cap() -> int:
    return _int("AUTOCRAWLER_FIRST_CRAWL_CAP", 30)


def ingest_rate() -> str:
    return _str("AUTOCRAWLER_INGEST_RATE", "2/s")


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
