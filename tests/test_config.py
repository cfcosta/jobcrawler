"""Tests for runtime configuration (§11).

Every env knob lives in config.py with a typed getter and a default. The
helpers carry the parsing; the getters are thin wrappers, so the tests pin
the parsing behavior plus a representative sample of getters.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from bizup_autocrawler import config


def test_int_knob_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_MAX_ITERATIONS", raising=False)
    assert config.max_iterations() == 20


def test_int_knob_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_MAX_ITERATIONS", "33")
    assert config.max_iterations() == 33


def test_invalid_int_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_MAX_PAGES", "notanint")
    with pytest.raises(ValueError):
        config.max_pages()


def test_float_knob_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_STABILITY_JACCARD", "0.75")
    assert config.stability_jaccard() == 0.75


@pytest.mark.parametrize("raw", ["on", "auto", "off"])
def test_review_gate_tristate_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() == raw


@pytest.mark.parametrize("raw", ["ON", " Auto ", "OFF"])
def test_review_gate_is_case_and_space_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", raw)
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
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() == expected


def test_review_gate_unknown_value_falls_back_to_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unrecognized value must fail safe to the most conservative mode, not silently
    # auto-promote untrusted generated code.
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", "banana")
    assert config.review_gate() == "on"


def test_review_gate_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    # Autonomous by default (§4.8 "full-auto, gated by proof"): an unset gate
    # auto-promotes a healed/bootstrapped crawler rather than staging it for a human.
    monkeypatch.delenv("AUTOCRAWLER_REVIEW_GATE", raising=False)
    assert config.review_gate() == "auto"


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_LOG_LEVEL", raising=False)
    assert config.log_level() == "INFO"


def test_log_level_is_normalized_upper(monkeypatch: pytest.MonkeyPatch) -> None:
    # Accept a lowercase env value but return a name logging.setLevel accepts.
    monkeypatch.setenv("AUTOCRAWLER_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"


def test_news_api_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWS_API_URL", raising=False)
    assert config.news_api_url() == "http://localhost:8000"


def test_tracking_denylist_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_TRACKING_PARAM_DENYLIST", "ref, src ,fbclid")
    denylist = config.tracking_param_denylist()
    assert {"ref", "src", "fbclid"} <= denylist


def test_optional_knob_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLARESOLVERR_URL", raising=False)
    assert config.flaresolverr_url() is None


def test_sub_model_required_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_SUB_MODEL", raising=False)
    assert config.sub_model() is None


def test_require_sub_model_errors_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.2/§11: an unset sub_lm silently bills every llm_query at root rates, so the
    # caller must error rather than fall back — never `sub_model() or model()`.
    monkeypatch.delenv("AUTOCRAWLER_SUB_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AUTOCRAWLER_SUB_MODEL"):
        config.require_sub_model()


def test_require_sub_model_returns_value_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_SUB_MODEL", "openrouter/cheap/model")
    assert config.require_sub_model() == "openrouter/cheap/model"


def test_fanout_spend_cap_usd_parses_dollar_per_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_FANOUT_SPEND_CAP", "$8/site")
    assert config.fanout_spend_cap_usd() == 8.0


def test_fanout_spend_cap_usd_accepts_bare_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_FANOUT_SPEND_CAP", "12.5")
    assert config.fanout_spend_cap_usd() == 12.5


def test_fanout_spend_cap_usd_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_FANOUT_SPEND_CAP", raising=False)
    assert config.fanout_spend_cap_usd() == 8.0


def test_crawl_isolation_knobs_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.4 committed-crawl subprocess bounds. Wall-clock is generous (a polite, paced
    # crawl spends most of its time asleep between fetches, accruing little CPU);
    # RLIMIT_CPU is tight (a compute spin is the real runaway); memory matches the
    # validation sandbox. The CPU cap is decoupled from wall-clock precisely so a
    # legitimately slow-but-paced crawl isn't mistaken for a runaway.
    for knob in (
        "AUTOCRAWLER_CRAWL_TIMEOUT",
        "AUTOCRAWLER_CRAWL_CPU_SECONDS",
        "AUTOCRAWLER_CRAWL_MEM_MB",
    ):
        monkeypatch.delenv(knob, raising=False)
    assert config.crawl_timeout() == 600.0
    assert config.crawl_cpu_seconds() == 120.0
    assert config.crawl_mem_mb() == 1024


def test_crawl_isolation_knobs_parse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_CRAWL_TIMEOUT", "90")
    monkeypatch.setenv("AUTOCRAWLER_CRAWL_CPU_SECONDS", "30")
    monkeypatch.setenv("AUTOCRAWLER_CRAWL_MEM_MB", "512")
    assert config.crawl_timeout() == 90.0
    assert config.crawl_cpu_seconds() == 30.0
    assert config.crawl_mem_mb() == 512


def test_fetch_timeout_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.5: per-request HTTP timeout for the live crawl/robots client, well under the
    # subprocess wall-clock so one hung socket can't eat the whole crawl budget.
    monkeypatch.delenv("AUTOCRAWLER_FETCH_TIMEOUT", raising=False)
    assert config.fetch_timeout() == 20.0
    monkeypatch.setenv("AUTOCRAWLER_FETCH_TIMEOUT", "5")
    assert config.fetch_timeout() == 5.0


def test_fetch_retry_knobs_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.5: a transient fetch (429/503/502/504 or a dropped connection) is retried a
    # bounded number of times with exponential backoff, capped so a hostile Retry-After
    # can't park a crawl for an hour.
    for knob in (
        "AUTOCRAWLER_FETCH_RETRIES",
        "AUTOCRAWLER_FETCH_BACKOFF",
        "AUTOCRAWLER_FETCH_RETRY_CAP",
    ):
        monkeypatch.delenv(knob, raising=False)
    assert config.fetch_retries() == 2
    assert config.fetch_backoff() == 0.5
    assert config.fetch_retry_cap() == 30.0
    monkeypatch.setenv("AUTOCRAWLER_FETCH_RETRIES", "5")
    monkeypatch.setenv("AUTOCRAWLER_FETCH_BACKOFF", "1.5")
    monkeypatch.setenv("AUTOCRAWLER_FETCH_RETRY_CAP", "60")
    assert config.fetch_retries() == 5
    assert config.fetch_backoff() == 1.5
    assert config.fetch_retry_cap() == 60.0


def test_solver_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.10: the safe default is no solver (plain httpx). The old "stealth" default
    # named an unbuilt backend, so make_solver() raised NotImplementedError on boot.
    monkeypatch.delenv("AUTOCRAWLER_SOLVER", raising=False)
    assert config.solver() == "none"


def test_model_optional_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_MODEL", raising=False)
    assert config.model() is None


def test_require_model_errors_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # §4.2/§11: AUTOCRAWLER_MODEL has no safe default (the old one was a non-existent
    # slug), so the generation/optimize path must error loud rather than fail opaque.
    monkeypatch.delenv("AUTOCRAWLER_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AUTOCRAWLER_MODEL"):
        config.require_model()


def test_require_model_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_MODEL", "openrouter/some/model")
    assert config.require_model() == "openrouter/some/model"


def test_reflection_model_optional_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_REFLECTION_MODEL", raising=False)
    assert config.reflection_model() is None


def test_require_reflection_model_errors_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §4.11/M5: GEPA's reflective mutation needs a reflection LM; build_gepa requires
    # a non-None one, so the gepa rung must error loud rather than build without it.
    monkeypatch.delenv("AUTOCRAWLER_REFLECTION_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AUTOCRAWLER_REFLECTION_MODEL"):
        config.require_reflection_model()


def test_require_reflection_model_returns_value_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    assert config.require_reflection_model() == "openrouter/strong/reflector"


def test_promotion_eval_passes_defaults_to_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_PROMOTION_EVAL_PASSES", raising=False)
    assert config.promotion_eval_passes() == 3


def test_promotion_eval_passes_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # A nonsensical 0/negative must not disable the promotion eval entirely.
    monkeypatch.setenv("AUTOCRAWLER_PROMOTION_EVAL_PASSES", "0")
    assert config.promotion_eval_passes() == 1


def test_crawlers_dir_defaults_to_in_package_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The committed crawlers live beside the package by default — where the editable
    # dev install resolves them and the baked example crawler lives.
    monkeypatch.delenv("AUTOCRAWLER_CRAWLERS_DIR", raising=False)
    assert config.crawlers_dir() == str(Path(config.__file__).parent / "crawlers")


def test_crawlers_dir_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # §11: a Nix/container deploy installs the package read-only in the store, so the
    # writable crawlers dir (the generate/promote/self-heal target, and where crawl
    # reads committed scripts) must be relocatable onto a writable volume.
    monkeypatch.setenv("AUTOCRAWLER_CRAWLERS_DIR", "/data/crawlers")
    assert config.crawlers_dir() == "/data/crawlers"


# --- adaptive cadence (§4.9 hit-rate controller) ---------------------------


def test_min_schedule_defaults_to_15m_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_MIN_SCHEDULE", raising=False)
    assert config.min_schedule() == 15 * 60


def test_max_schedule_defaults_to_1d_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_MAX_SCHEDULE", raising=False)
    assert config.max_schedule() == 24 * 60 * 60


def test_min_max_schedule_parse_duration_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_MIN_SCHEDULE", "5m")
    monkeypatch.setenv("AUTOCRAWLER_MAX_SCHEDULE", "6h")
    assert config.min_schedule() == 5 * 60
    assert config.max_schedule() == 6 * 60 * 60


def test_schedule_bounds_fall_back_on_malformed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in the bound must not crash the daemon at boot — fall back to default.
    monkeypatch.setenv("AUTOCRAWLER_MIN_SCHEDULE", "every-so-often")
    assert config.min_schedule() == 15 * 60


def test_adaptive_target_n_defaults_to_two(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_ADAPTIVE_TARGET_N", raising=False)
    assert config.adaptive_target_n() == 2.0


def test_adaptive_target_n_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # target_n < 1 would push the target hit rate above 1.0 (unreachable) — clamp.
    monkeypatch.setenv("AUTOCRAWLER_ADAPTIVE_TARGET_N", "0.5")
    assert config.adaptive_target_n() == 1.0


def test_adaptive_default_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fleet self-tunes cadence by default; a site opts out by pinning ``schedule:``
    # or ``adaptive: false``.
    monkeypatch.delenv("AUTOCRAWLER_ADAPTIVE_DEFAULT", raising=False)
    assert config.adaptive_default() is True


def test_adaptive_default_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_ADAPTIVE_DEFAULT", "false")
    assert config.adaptive_default() is False


def test_reopt_check_defaults_to_24h(monkeypatch: pytest.MonkeyPatch) -> None:
    # The offline §4.11 self-improvement loop is scheduled by default — a daily *check*,
    # gated so it only dispatches a compile when the trainset/pool are ready.
    monkeypatch.delenv("AUTOCRAWLER_REOPT_CHECK", raising=False)
    assert config.reopt_check() == "24h"


def test_reopt_check_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REOPT_CHECK", "off")
    assert config.reopt_check() == "off"


def test_adaptive_window_defaults_to_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_ADAPTIVE_WINDOW", raising=False)
    assert config.adaptive_window() == 8


def test_adaptive_window_floors_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_ADAPTIVE_WINDOW", "0")
    assert config.adaptive_window() == 1


def test_heal_cooldown_defaults_to_6h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_HEAL_COOLDOWN", raising=False)
    assert config.heal_cooldown() == "6h"


def test_heal_cooldown_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_HEAL_COOLDOWN", "off")
    assert config.heal_cooldown() == "off"


def test_autonomy_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The daemon self-improves out of the box; the kill-switch is an opt-out brake.
    monkeypatch.delenv("AUTOCRAWLER_AUTONOMY", raising=False)
    assert config.autonomy() is True


def test_autonomy_honors_env_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_AUTONOMY", "off")
    assert config.autonomy() is False


@pytest.mark.parametrize("raw", ["0", "false", "no"])
def test_autonomy_other_falsey_values_freeze(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_AUTONOMY", raw)
    assert config.autonomy() is False


def test_adopt_probation_defaults_to_48h(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_ADOPT_PROBATION", raising=False)
    assert config.adopt_probation() == timedelta(hours=48)


def test_adopt_probation_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_ADOPT_PROBATION", "12h")
    assert config.adopt_probation() == timedelta(hours=12)


@pytest.mark.parametrize("raw", ["off", "OFF", ""])
def test_adopt_probation_off_disables_the_watcher(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_ADOPT_PROBATION", raw)
    assert config.adopt_probation() is None


def test_adopt_probation_malformed_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo must not wedge the watcher (or crash the daemon) — fall back to 48h.
    monkeypatch.setenv("AUTOCRAWLER_ADOPT_PROBATION", "not-a-duration")
    assert config.adopt_probation() == timedelta(hours=48)


def test_adopt_regression_delta_defaults_to_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUTOCRAWLER_ADOPT_REGRESSION_DELTA", raising=False)
    assert config.adopt_regression_delta() == 2


def test_adopt_regression_delta_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_ADOPT_REGRESSION_DELTA", "5")
    assert config.adopt_regression_delta() == 5


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_adopt_regression_delta_is_floored_at_one(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # A 0/negative delta would make every adoption "regress" (post-pre ≥ 0 always) and
    # roll back instantly — floor it at 1 so the watcher can never self-trigger.
    monkeypatch.setenv("AUTOCRAWLER_ADOPT_REGRESSION_DELTA", raw)
    assert config.adopt_regression_delta() == 1


def test_body_quality_floor_defaults_to_half(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_BODY_QUALITY_FLOOR", raising=False)
    assert config.body_quality_floor() == 0.5


def test_body_quality_floor_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_BODY_QUALITY_FLOOR", "0.7")
    assert config.body_quality_floor() == 0.7


@pytest.mark.parametrize("raw", ["0", "0.0", "1.5", "-0.2"])
def test_body_quality_floor_rejects_out_of_range(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # A nonsensical floor (≤0 would heal nothing, >1 would heal always) falls back to
    # the safe default rather than disabling or over-triggering partial-drift healing.
    monkeypatch.setenv("AUTOCRAWLER_BODY_QUALITY_FLOOR", raw)
    assert config.body_quality_floor() == 0.5
