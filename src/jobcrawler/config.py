"""Runtime configuration for bizup-autocrawler, read from the environment (§11).

Every env knob the service reads lives here, so the full configuration surface
is visible in one place. All thresholds are host-side constants — never exposed
to the generator/RLM (the §8 hidden-scorer invariant). Getters are thin
wrappers over a few typed parsers.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from bizup_autocrawler.durations import parse_duration
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


# --- adaptive cadence (§4.9, the hit-rate controller) ----------------------
# A source flagged ``schedule: adaptive`` learns its own interval from recent
# hit/miss outcomes (see :mod:`bizup_autocrawler.adaptive`). These bound and
# tune that controller; all are host-side, never exposed to the generator (§8).


def _duration_secs(name: str, default: str) -> float:
    """An ``AUTOCRAWLER_*`` duration env var as float seconds (falls back on a
    malformed value to the default, so a typo can't crash the daemon at boot)."""
    raw = os.getenv(name)
    if raw is None:
        return parse_duration(default).total_seconds()
    try:
        return parse_duration(raw).total_seconds()
    except ValueError:
        return parse_duration(default).total_seconds()


def min_schedule() -> float:
    """Politeness floor (seconds) for an adaptive interval — the controller never
    shrinks below this no matter how busy a source is. Default 15m."""
    return _duration_secs("AUTOCRAWLER_MIN_SCHEDULE", "15m")


def max_schedule() -> float:
    """Staleness ceiling (seconds) for an adaptive interval — the controller never
    grows beyond this no matter how quiet a source is. Default 1d."""
    return _duration_secs("AUTOCRAWLER_MAX_SCHEDULE", "1d")


def adaptive_target_n() -> float:
    """Desired checks-per-post for an adaptive source: the controller steers the hit
    rate toward ``1 / target_n``. Default 2 (≈ check twice per post). Clamped ≥ 1 so
    the target hit rate stays in (0, 1]."""
    return max(1.0, _float("AUTOCRAWLER_ADAPTIVE_TARGET_N", 2.0))


def adaptive_default() -> bool:
    """When true, a site with no explicit ``schedule`` is adaptive rather than using
    the fixed ``default_schedule``. Default true — the fleet self-tunes each source's
    cadence to its hit-rate; pin ``schedule:`` or ``adaptive: false`` on a site to opt
    out."""
    return _bool("AUTOCRAWLER_ADAPTIVE_DEFAULT", True)


def adaptive_window() -> int:
    """How many recent crawls the hit-rate controller averages over. Default 8 —
    long enough to smooth single-crawl noise, short enough to track a real shift."""
    return max(1, _int("AUTOCRAWLER_ADAPTIVE_WINDOW", 8))


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
    return _bool("AUTOCRAWLER_AUTONOMY", True)


def regen_check() -> str:
    return _str("AUTOCRAWLER_REGEN_CHECK", "24h")


def heal_cooldown() -> str:
    """Minimum spacing between *event-driven* self-heals of one site (§4.8).

    A crawl that comes back blacked-out (zero items) or with collapsed body quality
    triggers an immediate staleness assessment from the run loop, instead of waiting
    for the next periodic ``AUTOCRAWLER_REGEN_CHECK`` sweep. This cooldown rate-limits
    that fast path per-site so a quick adaptive cadence (or an auto-promote that keeps
    re-drifting) can't re-run the generator every crawl. ``off`` disables event-driven
    healing entirely, leaving only the periodic sweep. Default 6h."""
    return _str("AUTOCRAWLER_HEAL_COOLDOWN", "6h")


def body_quality_floor() -> float:
    """Fraction of complete-bodied items below which a crawl is a partial-drift signal.

    ``body_quality`` is the share of fetched items whose body cleared the completeness
    bar (§4.6); it sags when a site starts serving teasers/paywalls even while item
    counts stay healthy. A crawl at or above this floor is fine; below it (with items
    found) is a drift candidate. Host-side only — never surfaced to the generator (§8).
    Default 0.5. Clamped to (0, 1]; an out-of-range value falls back to the default."""
    value = _float("AUTOCRAWLER_BODY_QUALITY_FLOOR", 0.5)
    return value if 0.0 < value <= 1.0 else 0.5


def genfail_backoff() -> float:
    """Base delay (seconds) before a generation-failed site is re-tried (§4.8).

    A site whose regeneration exhausted its budget is held, not abandoned: after this
    backoff it is re-tried with the *current* generator (which the offline §4.11 loop
    keeps improving). The delay grows geometrically per consecutive failure (×2 each)
    up to ``genfail_backoff_cap``, so a chronically broken site backs off without ever
    giving up. Default 6h."""
    return _duration_secs("AUTOCRAWLER_GENFAIL_BACKOFF", "6h")


def genfail_backoff_cap() -> float:
    """Ceiling (seconds) for the generation-failed retry backoff. Default 7d."""
    return _duration_secs("AUTOCRAWLER_GENFAIL_BACKOFF_CAP", "7d")


def reopt_check() -> str:
    """How often the scheduler checks whether to dispatch the offline §4.11 GEPA
    self-improvement job (out-of-band; ``.compile()`` never runs in-band). Default 24h;
    ``off`` disables it. A check only *dispatches* when the readiness gates (trainset
    growth + pool) pass and no compile is already running, so a daily check is not a
    daily compile — and an unconfigured optimizer model just no-ops the dispatch."""
    return _str("AUTOCRAWLER_REOPT_CHECK", "24h")


def optimize_lock_path() -> str:
    """Single-flight lockfile for the offline §4.11 optimize job (held for its run).

    The GEPA compile is multi-hour; ``optimize_job`` holds an advisory lock on this
    file for the whole run so a reopt tick that fires while a previous compile is
    still going is a no-op rather than a pile-up of concurrent runs fighting over the
    same trainset and generator dir. CWD-relative by default, like the trainset."""
    return _str("AUTOCRAWLER_OPTIMIZE_LOCK", "optimize.lock")


def optimize_log_path() -> str:
    """Where the detached out-of-band optimize subprocess appends its stdout/stderr.

    The §4.11 hand-off runs ``autocrawl optimize`` detached (it outlives the daemon),
    so its progress and the final PROMOTED / not-promoted line land here rather than
    on the daemon's console. CWD-relative by default, like the trainset."""
    return _str("AUTOCRAWLER_OPTIMIZE_LOG", "optimize.log")


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
    raw = _str("AUTOCRAWLER_ADOPT_PROBATION", "48h")
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
    return max(1, _int("AUTOCRAWLER_ADOPT_REGRESSION_DELTA", 2))


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


def review_gate() -> str:
    """Tri-state §4.8 self-heal & §4.2 cold-start delivery policy (default ``auto``):

      * ``auto`` — auto-promote (the default): back up the current live crawler to
        ``crawlers/_history/<slug>/`` then write the new one live, so the site
        self-heals — and a brand-new site bootstraps — unattended. Safety is the §6
        gates plus versioned rollback (``autocrawl rollback``), not a human (the §4.8
        "full-auto, gated by proof").
      * ``on`` — stage a crawler to ``crawlers/_pending/<slug>.py`` and mark the site
        pending-review; an operator runs ``autocrawl promote``. A human approves every
        live change (most conservative).
      * ``off`` — write live with no backup (operator-trusted; no rollback copy).

    The legacy boolean values are still accepted: truthy ⇒ ``on`` (the prior staging
    behavior), falsy ⇒ ``off`` (the prior go-live), so a deployment that set those keeps
    its behavior. An *unset* gate now defaults to ``auto`` (autonomous); an unrecognized
    value still falls back to ``on`` (the safe, human-gated path).
    """
    raw = os.getenv("AUTOCRAWLER_REVIEW_GATE")
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
    """Root log level for the daemon/CLI (``AUTOCRAWLER_LOG_LEVEL``; default INFO).

    Upper-cased so a lowercase value still names a level ``logging.setLevel`` accepts.
    """
    return _str("AUTOCRAWLER_LOG_LEVEL", "INFO").upper()


def notify_webhook() -> str | None:
    """Optional webhook for operator alerts on autonomous actions (None = log only).

    When ``AUTOCRAWLER_NOTIFY_WEBHOOK`` is an ``http(s)`` URL, every self-modifying
    action (§4.8 heal/promote, §4.11 adopt, generator rollback) POSTs a small JSON
    payload there in addition to the log line, so an operator gets pushed an alert
    when the daemon changes its own code without having to tail logs. Best-effort and
    fire-and-forget: a webhook that is slow or down never blocks or aborts the action
    (see :mod:`bizup_autocrawler.notify`). Unset → notifications are logged only."""
    return _opt("AUTOCRAWLER_NOTIFY_WEBHOOK")


def notify_timeout() -> float:
    """Per-POST timeout (seconds) for the notify webhook (``AUTOCRAWLER_NOTIFY_TIMEOUT``).

    Bounds how long a single best-effort delivery attempt waits before giving up, so a
    hung receiver can't pile up background threads. Default 10s."""
    return _float("AUTOCRAWLER_NOTIFY_TIMEOUT", 10.0)
