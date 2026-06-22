"""Tests for runtime configuration (§11).

Every env knob lives in config.py with a typed getter and a default. The
helpers carry the parsing; the getters are thin wrappers, so the tests pin
the parsing behavior plus a representative sample of getters.
"""

from __future__ import annotations

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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("on", True),
        ("1", True),
        ("true", True),
        ("off", False),
        ("0", False),
        ("no", False),
    ],
)
def test_bool_knob(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", raw)
    assert config.review_gate() is expected


def test_review_gate_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_REVIEW_GATE", raising=False)
    assert config.review_gate() is True


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
