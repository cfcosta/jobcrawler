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
