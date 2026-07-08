"""Tests for runtime configuration (§11).

Every env knob lives in config.py with a typed getter and a default. The
helpers carry the parsing; the getters are thin wrappers, so the tests pin
the parsing behavior plus a representative sample of getters.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from jobcrawler import config


def test_int_knob_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MAX_ITERATIONS", raising=False)
    assert config.max_iterations() == 20


def test_int_knob_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MAX_ITERATIONS", "33")
    assert config.max_iterations() == 33


def test_invalid_int_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MAX_PAGES", "notanint")
    with pytest.raises(ValueError):
        config.max_pages()


def test_float_knob_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_STABILITY_JACCARD", "0.75")
    assert config.stability_jaccard() == 0.75


@pytest.mark.parametrize("raw", ["on", "auto", "off"])
def test_review_gate_tristate_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() == raw


@pytest.mark.parametrize("raw", ["ON", " Auto ", "OFF"])
def test_review_gate_is_case_and_space_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() == raw.strip().lower()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", "on"),
        ("true", "on"),
        ("yes", "on"),
        ("0", "off"),
        ("false", "off"),
        ("no", "off"),
    ],
)
def test_review_gate_legacy_boolean_back_compat(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    # Pre-tri-state deployments set the gate with boolean-ish strings; truthy maps to the
    # safe staging mode ("on"), falsy to "off". "auto" is opt-in only, never inferred.
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() == expected


def test_review_gate_unknown_value_falls_back_to_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unrecognized value must fail safe to the most conservative mode, not silently
    # auto-promote untrusted generated code.
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", "banana")
    assert config.review_gate() == "on"


def test_review_gate_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Autonomous by default (§4.8 "full-auto, gated by proof"): an unset gate
    # auto-promotes a healed/bootstrapped crawler rather than staging it for a human.
    monkeypatch.delenv("JOBCRAWLER_REVIEW_GATE", raising=False)
    assert config.review_gate() == "auto"


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_LOG_LEVEL", raising=False)
    assert config.log_level() == "INFO"


def test_log_level_is_normalized_upper(monkeypatch: pytest.MonkeyPatch) -> None:
    # Accept a lowercase env value but return a name logging.setLevel accepts.
    monkeypatch.setenv("JOBCRAWLER_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"


def test_user_agent_default_identifies_job_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The UA advertises a job tracker (not the old news aggregator) so an operator
    # reading their access logs sees what is actually crawling them.
    monkeypatch.delenv("JOBCRAWLER_USER_AGENT", raising=False)
    assert config.user_agent() == "jobcrawler/0.1 (+job tracker)"


def test_user_agent_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_USER_AGENT", "acme-bot/2.0 (+ops@acme)")
    assert config.user_agent() == "acme-bot/2.0 (+ops@acme)"


def test_state_url_defaults_to_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_STATE_URL", raising=False)
    assert config.state_url() == "sqlite:///jobcrawler.db"


def test_tracking_denylist_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_TRACKING_PARAM_DENYLIST", "ref, src ,fbclid")
    denylist = config.tracking_param_denylist()
    assert {"ref", "src", "fbclid"} <= denylist


def test_optional_knob_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLARESOLVERR_URL", raising=False)
    assert config.flaresolverr_url() is None


def test_sub_model_required_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_SUB_MODEL", raising=False)
    assert config.sub_model() is None


def test_require_sub_model_errors_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.2/§11: an unset sub_lm silently bills every llm_query at root rates, so the
    # caller must error rather than fall back — never `sub_model() or model()`.
    monkeypatch.delenv("JOBCRAWLER_SUB_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="JOBCRAWLER_SUB_MODEL"):
        config.require_sub_model()


def test_require_sub_model_returns_value_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_SUB_MODEL", "openrouter/cheap/model")
    assert config.require_sub_model() == "openrouter/cheap/model"


def test_fanout_token_cap_parses_m_suffix_per_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_FANOUT_TOKEN_CAP", "1.5M/site")
    assert config.fanout_token_cap() == 1_500_000


def test_fanout_token_cap_accepts_k_suffix_and_bare_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_FANOUT_TOKEN_CAP", "750k")
    assert config.fanout_token_cap() == 750_000
    monkeypatch.setenv("JOBCRAWLER_FANOUT_TOKEN_CAP", "2000000")
    assert config.fanout_token_cap() == 2_000_000


def test_fanout_token_cap_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_FANOUT_TOKEN_CAP", raising=False)
    assert config.fanout_token_cap() == 2_000_000


def test_crawl_isolation_knobs_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.4 committed-crawl subprocess bounds. Wall-clock is generous (a polite, paced
    # crawl spends most of its time asleep between fetches, accruing little CPU);
    # RLIMIT_CPU is tight (a compute spin is the real runaway); memory matches the
    # validation sandbox. The CPU cap is decoupled from wall-clock precisely so a
    # legitimately slow-but-paced crawl isn't mistaken for a runaway.
    for knob in (
        "JOBCRAWLER_CRAWL_TIMEOUT",
        "JOBCRAWLER_CRAWL_CPU_SECONDS",
        "JOBCRAWLER_CRAWL_MEM_MB",
    ):
        monkeypatch.delenv(knob, raising=False)
    assert config.crawl_timeout() == 600.0
    assert config.crawl_cpu_seconds() == 120.0
    assert config.crawl_mem_mb() == 1024


def test_crawl_isolation_knobs_parse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_CRAWL_TIMEOUT", "90")
    monkeypatch.setenv("JOBCRAWLER_CRAWL_CPU_SECONDS", "30")
    monkeypatch.setenv("JOBCRAWLER_CRAWL_MEM_MB", "512")
    assert config.crawl_timeout() == 90.0
    assert config.crawl_cpu_seconds() == 30.0
    assert config.crawl_mem_mb() == 512


def test_fetch_timeout_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.5: per-request HTTP timeout for the live crawl/robots client, well under the
    # subprocess wall-clock so one hung socket can't eat the whole crawl budget.
    monkeypatch.delenv("JOBCRAWLER_FETCH_TIMEOUT", raising=False)
    assert config.fetch_timeout() == 20.0
    monkeypatch.setenv("JOBCRAWLER_FETCH_TIMEOUT", "5")
    assert config.fetch_timeout() == 5.0


def test_fetch_retry_knobs_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.5: a transient fetch (429/503/502/504 or a dropped connection) is retried a
    # bounded number of times with exponential backoff, capped so a hostile Retry-After
    # can't park a crawl for an hour.
    for knob in (
        "JOBCRAWLER_FETCH_RETRIES",
        "JOBCRAWLER_FETCH_BACKOFF",
        "JOBCRAWLER_FETCH_RETRY_CAP",
    ):
        monkeypatch.delenv(knob, raising=False)
    assert config.fetch_retries() == 2
    assert config.fetch_backoff() == 0.5
    assert config.fetch_retry_cap() == 30.0
    monkeypatch.setenv("JOBCRAWLER_FETCH_RETRIES", "5")
    monkeypatch.setenv("JOBCRAWLER_FETCH_BACKOFF", "1.5")
    monkeypatch.setenv("JOBCRAWLER_FETCH_RETRY_CAP", "60")
    assert config.fetch_retries() == 5
    assert config.fetch_backoff() == 1.5
    assert config.fetch_retry_cap() == 60.0


def test_solver_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.10: the safe default is no solver (plain httpx). The old "stealth" default
    # named an unbuilt backend, so make_solver() raised NotImplementedError on boot.
    monkeypatch.delenv("JOBCRAWLER_SOLVER", raising=False)
    assert config.solver() == "none"


def test_model_optional_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MODEL", raising=False)
    assert config.model() is None


def test_require_model_errors_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.2/§11: JOBCRAWLER_MODEL has no safe default (the old one was a non-existent
    # slug), so the generation/optimize path must error loud rather than fail opaque.
    monkeypatch.delenv("JOBCRAWLER_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="JOBCRAWLER_MODEL"):
        config.require_model()


def test_require_model_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MODEL", "openrouter/some/model")
    assert config.require_model() == "openrouter/some/model"


def test_reflection_model_optional_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_REFLECTION_MODEL", raising=False)
    assert config.reflection_model() is None


def test_require_reflection_model_errors_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §4.11/M5: GEPA's reflective mutation needs a reflection LM; build_gepa requires
    # a non-None one, so the gepa rung must error loud rather than build without it.
    monkeypatch.delenv("JOBCRAWLER_REFLECTION_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="JOBCRAWLER_REFLECTION_MODEL"):
        config.require_reflection_model()


def test_require_reflection_model_returns_value_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    assert config.require_reflection_model() == "openrouter/strong/reflector"


def test_auth_dir_prefers_jobcrawler_then_litellm_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ChatGPT-subscription OAuth token store (§9): JOBCRAWLER_AUTH_DIR wins, then
    # litellm's own CHATGPT_TOKEN_DIR is honored, else a jobcrawler-owned ~/.config default.
    monkeypatch.delenv("JOBCRAWLER_AUTH_DIR", raising=False)
    monkeypatch.delenv("CHATGPT_TOKEN_DIR", raising=False)
    assert config.auth_dir().endswith("/.config/jobcrawler/chatgpt")

    monkeypatch.setenv("CHATGPT_TOKEN_DIR", "/litellm/store")
    assert config.auth_dir() == "/litellm/store"

    monkeypatch.setenv("JOBCRAWLER_AUTH_DIR", "/jobcrawler/store")
    assert config.auth_dir() == "/jobcrawler/store"  # wins over CHATGPT_TOKEN_DIR


def test_promotion_eval_passes_defaults_to_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_PROMOTION_EVAL_PASSES", raising=False)
    assert config.promotion_eval_passes() == 3


def test_promotion_eval_passes_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # A nonsensical 0/negative must not disable the promotion eval entirely.
    monkeypatch.setenv("JOBCRAWLER_PROMOTION_EVAL_PASSES", "0")
    assert config.promotion_eval_passes() == 1


def test_crawlers_dir_defaults_to_in_package_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The committed crawlers live beside the package by default — where the editable
    # dev install resolves them and the baked example crawler lives.
    monkeypatch.delenv("JOBCRAWLER_CRAWLERS_DIR", raising=False)
    assert config.crawlers_dir() == str(Path(config.__file__).parent / "crawlers")


def test_crawlers_dir_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # §11: a Nix/container deploy installs the package read-only in the store, so the
    # writable crawlers dir (the generate/promote/self-heal target, and where crawl
    # reads committed scripts) must be relocatable onto a writable volume.
    monkeypatch.setenv("JOBCRAWLER_CRAWLERS_DIR", "/data/crawlers")
    assert config.crawlers_dir() == "/data/crawlers"


# --- adaptive cadence (§4.9 hit-rate controller) ---------------------------


def test_min_schedule_defaults_to_30m_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Job postings don't churn minute-to-minute; the adaptive politeness floor rises
    # from the news-era 15m to a half-hour floor.
    monkeypatch.delenv("JOBCRAWLER_MIN_SCHEDULE", raising=False)
    assert config.min_schedule() == 30 * 60


def test_max_schedule_defaults_to_3d_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A job stays open for weeks, so even a near-silent board is fresh checked every
    # three days — the staleness ceiling rises from the news-era 1d.
    monkeypatch.delenv("JOBCRAWLER_MAX_SCHEDULE", raising=False)
    assert config.max_schedule() == 3 * 24 * 60 * 60


def test_min_max_schedule_parse_duration_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MIN_SCHEDULE", "5m")
    monkeypatch.setenv("JOBCRAWLER_MAX_SCHEDULE", "6h")
    assert config.min_schedule() == 5 * 60
    assert config.max_schedule() == 6 * 60 * 60


def test_schedule_bounds_fall_back_on_malformed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the bound must not crash the daemon at boot — fall back to default.
    monkeypatch.setenv("JOBCRAWLER_MIN_SCHEDULE", "every-so-often")
    assert config.min_schedule() == 30 * 60


def test_adaptive_target_n_defaults_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_ADAPTIVE_TARGET_N", raising=False)
    assert config.adaptive_target_n() == 2.0


def test_adaptive_target_n_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # target_n < 1 would push the target hit rate above 1.0 (unreachable) — clamp.
    monkeypatch.setenv("JOBCRAWLER_ADAPTIVE_TARGET_N", "0.5")
    assert config.adaptive_target_n() == 1.0


def test_adaptive_default_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fleet self-tunes cadence by default; a site opts out by pinning ``schedule:``
    # or ``adaptive: false``.
    monkeypatch.delenv("JOBCRAWLER_ADAPTIVE_DEFAULT", raising=False)
    assert config.adaptive_default() is True


def test_adaptive_default_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_ADAPTIVE_DEFAULT", "false")
    assert config.adaptive_default() is False


def test_reopt_check_defaults_to_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    # The offline §4.11 self-improvement loop is scheduled by default — a daily *check*,
    # gated so it only dispatches a compile when the trainset/pool are ready.
    monkeypatch.delenv("JOBCRAWLER_REOPT_CHECK", raising=False)
    assert config.reopt_check() == "24h"


def test_reopt_check_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_REOPT_CHECK", "off")
    assert config.reopt_check() == "off"


def test_adaptive_window_defaults_to_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_ADAPTIVE_WINDOW", raising=False)
    assert config.adaptive_window() == 8


def test_adaptive_window_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_ADAPTIVE_WINDOW", "0")
    assert config.adaptive_window() == 1


def test_heal_cooldown_defaults_to_6h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_HEAL_COOLDOWN", raising=False)
    assert config.heal_cooldown() == "6h"


def test_heal_cooldown_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_HEAL_COOLDOWN", "off")
    assert config.heal_cooldown() == "off"


def test_autonomy_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The daemon self-improves out of the box; the kill-switch is an opt-out brake.
    monkeypatch.delenv("JOBCRAWLER_AUTONOMY", raising=False)
    assert config.autonomy() is True


def test_autonomy_honors_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_AUTONOMY", "off")
    assert config.autonomy() is False


@pytest.mark.parametrize("raw", ["0", "false", "no"])
def test_autonomy_other_falsey_values_freeze(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("JOBCRAWLER_AUTONOMY", raw)
    assert config.autonomy() is False


def test_adopt_probation_defaults_to_48h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_ADOPT_PROBATION", raising=False)
    assert config.adopt_probation() == timedelta(hours=48)


def test_adopt_probation_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_ADOPT_PROBATION", "12h")
    assert config.adopt_probation() == timedelta(hours=12)


@pytest.mark.parametrize("raw", ["off", "OFF", ""])
def test_adopt_probation_off_disables_the_watcher(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("JOBCRAWLER_ADOPT_PROBATION", raw)
    assert config.adopt_probation() is None


def test_adopt_probation_malformed_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo must not wedge the watcher (or crash the daemon) — fall back to 48h.
    monkeypatch.setenv("JOBCRAWLER_ADOPT_PROBATION", "not-a-duration")
    assert config.adopt_probation() == timedelta(hours=48)


def test_adopt_regression_delta_defaults_to_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_ADOPT_REGRESSION_DELTA", raising=False)
    assert config.adopt_regression_delta() == 2


def test_adopt_regression_delta_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_ADOPT_REGRESSION_DELTA", "5")
    assert config.adopt_regression_delta() == 5


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_adopt_regression_delta_is_floored_at_one(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # A 0/negative delta would make every adoption "regress" (post-pre ≥ 0 always) and
    # roll back instantly — floor it at 1 so the watcher can never self-trigger.
    monkeypatch.setenv("JOBCRAWLER_ADOPT_REGRESSION_DELTA", raw)
    assert config.adopt_regression_delta() == 1


def test_description_quality_floor_defaults_to_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR", raising=False)
    assert config.description_quality_floor() == 0.5


def test_description_quality_floor_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR", "0.7")
    assert config.description_quality_floor() == 0.7


@pytest.mark.parametrize("raw", ["0", "0.0", "1.5", "-0.2"])
def test_description_quality_floor_rejects_out_of_range(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # A nonsensical floor (≤0 would heal nothing, >1 would heal always) falls back to
    # the safe default rather than disabling or over-triggering partial-drift healing.
    monkeypatch.setenv("JOBCRAWLER_DESCRIPTION_QUALITY_FLOOR", raw)
    assert config.description_quality_floor() == 0.5


# --- gate thresholds & completeness (§6/§4.6) ------------------------------


def test_min_items_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # A two-role startup careers page is a complete board, so the absolute floor drops
    # from the news-era 5 to 1; completeness is carried by the §6 enum-floor + stability
    # gates instead. Only the truly empty crawl fails the absolute floor now.
    monkeypatch.delenv("JOBCRAWLER_MIN_ITEMS", raising=False)
    assert config.min_items() == 1


def test_min_items_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MIN_ITEMS", "3")
    assert config.min_items() == 3


def test_min_description_chars_defaults_to_300(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Down from the news-era 400: a tight-but-real engineering posting clears 300, a
    # title-only stub or "see our site" redirect does not.
    monkeypatch.delenv("JOBCRAWLER_MIN_DESCRIPTION_CHARS", raising=False)
    assert config.min_description_chars() == 300


def test_min_description_chars_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MIN_DESCRIPTION_CHARS", "500")
    assert config.min_description_chars() == 500


def test_min_description_quality_defaults_to_point_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOBCRAWLER_MIN_DESCRIPTION_QUALITY", raising=False)
    assert config.min_description_quality() == 0.6


def test_min_description_quality_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MIN_DESCRIPTION_QUALITY", "0.8")
    assert config.min_description_quality() == 0.8


def test_min_unique_description_chars_defaults_to_150(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Catches a description that clears the length floor but is boilerplate padding (a
    # repeated blurb, a wall of one char) where the distinct-char set stays tiny.
    monkeypatch.delenv("JOBCRAWLER_MIN_UNIQUE_DESCRIPTION_CHARS", raising=False)
    assert config.min_unique_description_chars() == 150


def test_min_unique_description_chars_parses_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOBCRAWLER_MIN_UNIQUE_DESCRIPTION_CHARS", "80")
    assert config.min_unique_description_chars() == 80


# --- expired markers (mirrors the old paywall-marker |-split pattern) ------


def test_expired_markers_defaults_cover_en_and_pt_br(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page can outlive its opening; these EN + pt-BR markers drop a still-served but
    # dead posting at ingest, catching what the enumerate-diff closure cannot.
    monkeypatch.delenv("JOBCRAWLER_EXPIRED_MARKERS", raising=False)
    markers = config.expired_markers()
    assert "no longer accepting applications" in markers
    assert "this position has been filled" in markers
    assert "position is no longer available" in markers
    assert "vaga encerrada" in markers
    assert "vaga indisponível" in markers


def test_expired_markers_env_overrides_pipe_separated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `|`-separated, whitespace-trimmed, blanks dropped — mirrors the old paywall knob.
    monkeypatch.setenv(
        "JOBCRAWLER_EXPIRED_MARKERS", " closed | filled ||  vaga fechada "
    )
    assert config.expired_markers() == ("closed", "filled", "vaga fechada")


def test_expired_markers_empty_env_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit empty value disables the filter entirely (operator opt-out), distinct
    # from unset (which yields the defaults).
    monkeypatch.setenv("JOBCRAWLER_EXPIRED_MARKERS", "")
    assert config.expired_markers() == ()


# --- position lifecycle: closure & recheck (§4.7/§4.9) ---------------------


def test_close_after_misses_defaults_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    # One absence is often a transient hiccup; two consecutive misses confirm a role is
    # genuinely gone before it is closed.
    monkeypatch.delenv("JOBCRAWLER_CLOSE_AFTER_MISSES", raising=False)
    assert config.close_after_misses() == 2


def test_close_after_misses_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_CLOSE_AFTER_MISSES", "3")
    assert config.close_after_misses() == 3


def test_recheck_interval_defaults_to_12h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_RECHECK_INTERVAL", raising=False)
    assert config.recheck_interval() == "12h"


def test_recheck_interval_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_RECHECK_INTERVAL", "6h")
    assert config.recheck_interval() == "6h"


def test_recheck_cap_defaults_to_300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_RECHECK_CAP", raising=False)
    assert config.recheck_cap() == 300


def test_assess_timeout_defaults_to_30m(monkeypatch: pytest.MonkeyPatch) -> None:
    # The §4.9 liveness watchdog: one wedged generation must not freeze the loop.
    monkeypatch.delenv("JOBCRAWLER_ASSESS_TIMEOUT", raising=False)
    assert config.assess_timeout() == 30 * 60
    monkeypatch.setenv("JOBCRAWLER_ASSESS_TIMEOUT", "0s")
    assert config.assess_timeout() == 0.0  # 0s disables the bound


def test_recheck_cap_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_RECHECK_CAP", "500")
    assert config.recheck_cap() == 500


def test_default_schedule_defaults_to_2h(monkeypatch: pytest.MonkeyPatch) -> None:
    # Up from the news-era 30m: job boards move on the order of days, so a 2h base is
    # fresh enough while staying polite.
    monkeypatch.delenv("JOBCRAWLER_DEFAULT_SCHEDULE", raising=False)
    assert config.default_schedule() == "2h"


def test_default_schedule_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_DEFAULT_SCHEDULE", "1h")
    assert config.default_schedule() == "1h"


# --- profile & host-side matcher (§4.6) ------------------------------------


def test_profile_path_defaults_to_profile_md(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_PROFILE", raising=False)
    assert config.profile_path() == "PROFILE.md"


def test_profile_path_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_PROFILE", "/data/me.md")
    assert config.profile_path() == "/data/me.md"


def test_match_model_optional_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MATCH_MODEL", raising=False)
    assert config.match_model() is None


def test_match_model_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_MODEL", "openrouter/cheap/matcher")
    assert config.match_model() == "openrouter/cheap/matcher"


def test_require_match_model_prefers_match_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit match model wins over the sub_lm fallback even when both are set.
    monkeypatch.setenv("JOBCRAWLER_MATCH_MODEL", "openrouter/cheap/matcher")
    monkeypatch.setenv("JOBCRAWLER_SUB_MODEL", "openrouter/cheap/sub")
    assert config.require_match_model() == "openrouter/cheap/matcher"


def test_require_match_model_falls_back_to_sub_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Matching bills tokens per job, so an unset match model borrows the cheap sub_lm
    # rather than the expensive root planner.
    monkeypatch.delenv("JOBCRAWLER_MATCH_MODEL", raising=False)
    monkeypatch.setenv("JOBCRAWLER_SUB_MODEL", "openrouter/cheap/sub")
    assert config.require_match_model() == "openrouter/cheap/sub"


def test_require_match_model_never_uses_root_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With only the root planner set (no match/sub model), matching must error rather
    # than fall back to the ruinously-priced planner — the whole point of the fallback
    # chain stopping at sub_lm.
    monkeypatch.delenv("JOBCRAWLER_MATCH_MODEL", raising=False)
    monkeypatch.delenv("JOBCRAWLER_SUB_MODEL", raising=False)
    monkeypatch.setenv("JOBCRAWLER_MODEL", "openrouter/expensive/planner")
    with pytest.raises(RuntimeError, match="JOBCRAWLER_MATCH_MODEL"):
        config.require_match_model()


def test_require_match_model_error_names_sub_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The error names both env vars so an operator knows either one satisfies it.
    monkeypatch.delenv("JOBCRAWLER_MATCH_MODEL", raising=False)
    monkeypatch.delenv("JOBCRAWLER_SUB_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="JOBCRAWLER_SUB_MODEL"):
        config.require_match_model()


def test_match_batch_defaults_to_ten(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MATCH_BATCH", raising=False)
    assert config.match_batch() == 10


def test_match_batch_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_BATCH", "25")
    assert config.match_batch() == 25


def test_match_min_score_defaults_to_70(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MATCH_MIN_SCORE", raising=False)
    assert config.match_min_score() == 70


def test_match_min_score_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_MIN_SCORE", "85")
    assert config.match_min_score() == 85


def test_match_daily_cap_defaults_to_800(monkeypatch: pytest.MonkeyPatch) -> None:
    # The hard token-budget backstop: cache hits are free, only LLM-scored jobs count.
    monkeypatch.delenv("JOBCRAWLER_MATCH_DAILY_CAP", raising=False)
    assert config.match_daily_cap() == 800


def test_match_daily_cap_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_DAILY_CAP", "1000")
    assert config.match_daily_cap() == 1000


def test_match_rate_defaults_to_20_per_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_MATCH_RATE", raising=False)
    assert config.match_rate() == "20/m"


def test_match_rate_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_MATCH_RATE", "30/m")
    assert config.match_rate() == "30/m"


def test_match_max_tokens_default_and_override(monkeypatch) -> None:
    # Distinct from the generation LM's JOBCRAWLER_MAX_TOKENS: a verdict batch needs
    # ~4k, and the ceiling must stay below a low credit balance or providers reject
    # every match call outright.
    monkeypatch.delenv("JOBCRAWLER_MATCH_MAX_TOKENS", raising=False)
    assert config.match_max_tokens() == 4000
    monkeypatch.setenv("JOBCRAWLER_MATCH_MAX_TOKENS", "2500")
    assert config.match_max_tokens() == 2500


# -- web UI (jobcrawl serve, §16) --------------------------------------------


def test_serve_host_defaults_to_loopback(monkeypatch) -> None:
    # Personal application data must not be network-exposed by default (§16).
    monkeypatch.delenv("JOBCRAWLER_SERVE_HOST", raising=False)
    assert config.serve_host() == "127.0.0.1"


def test_serve_host_override(monkeypatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_SERVE_HOST", "0.0.0.0")
    assert config.serve_host() == "0.0.0.0"


def test_serve_port_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("JOBCRAWLER_SERVE_PORT", raising=False)
    assert config.serve_port() == 8765
    monkeypatch.setenv("JOBCRAWLER_SERVE_PORT", "9100")
    assert config.serve_port() == 9100


def test_serve_port_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_SERVE_PORT", "notaport")
    with pytest.raises(ValueError):
        config.serve_port()
