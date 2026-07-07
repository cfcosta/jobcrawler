"""Tests for the out-of-band currency→USD rate fetch/refresh (§4.6).

The fetcher pulls a USD-based feed and inverts it to the USD-per-unit factors the salary
conversion multiplies by; the refresh wrapper is fail-soft so a bad pull never overwrites
good stored rates nor interrupts the daemon loop.
"""

from __future__ import annotations

import httpx
import pytest

from jobcrawler import fx
from jobcrawler.state import StateStore


def _resp(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json=payload, request=httpx.Request("GET", "https://fx.test/latest")
    )


def _patch_get(monkeypatch: pytest.MonkeyPatch, resp: httpx.Response) -> None:
    monkeypatch.setattr(fx.httpx, "get", lambda *a, **k: resp)


def test_fetch_rates_inverts_usd_based_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Feed shape: rates are units-per-USD; we store USD-per-unit (1/N), USD the identity.
    payload = {
        "result": "success",
        "rates": {"USD": 1.0, "EUR": 0.8, "GBP": 0.5, "BRL": 5.0},
    }
    _patch_get(monkeypatch, _resp(payload))
    rates = fx.fetch_rates("https://fx.test/latest")
    assert rates["USD"] == 1.0
    assert rates["EUR"] == pytest.approx(1.25)  # 1 / 0.8
    assert rates["GBP"] == pytest.approx(2.0)  # 1 / 0.5
    assert rates["BRL"] == pytest.approx(0.2)  # 1 / 5.0


def test_fetch_rates_rejects_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _resp({"result": "error", "error-type": "unsupported-code"}))
    with pytest.raises(RuntimeError):
        fx.fetch_rates("https://fx.test/latest")


def test_fetch_rates_rejects_response_missing_majors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # EUR present but GBP/BRL absent — a partial payload must not narrow coverage silently.
    _patch_get(monkeypatch, _resp({"result": "success", "rates": {"USD": 1.0, "EUR": 0.8}}))
    with pytest.raises(RuntimeError):
        fx.fetch_rates("https://fx.test/latest")


def test_fetch_rates_skips_nonpositive_and_nonnumeric_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "result": "success",
        "rates": {"EUR": 0.8, "GBP": 0.5, "BRL": 5.0, "XXX": 0, "YYY": "n/a"},
    }
    _patch_get(monkeypatch, _resp(payload))
    rates = fx.fetch_rates("https://fx.test/latest")
    assert "XXX" not in rates  # zero quote skipped (no divide-by-zero)
    assert "YYY" not in rates  # non-numeric skipped


def test_fetch_rates_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, _resp({}, status=503))
    with pytest.raises(httpx.HTTPStatusError):
        fx.fetch_rates("https://fx.test/latest")


def test_refresh_rates_persists_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"result": "success", "rates": {"EUR": 0.8, "GBP": 0.5, "BRL": 5.0}}
    _patch_get(monkeypatch, _resp(payload))
    store = StateStore(":memory:")
    try:
        rates = fx.refresh_rates(store)
        assert rates["EUR"] == pytest.approx(1.25)
        assert store.get_fx_rates()["EUR"] == pytest.approx(1.25)
    finally:
        store.close()


def test_refresh_rates_is_failsoft_and_keeps_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(":memory:")
    try:
        store.set_fx_rates({"EUR": 1.1, "GBP": 1.3, "BRL": 0.2})

        def boom(*a: object, **k: object) -> httpx.Response:
            raise httpx.ConnectError("endpoint down")

        monkeypatch.setattr(fx.httpx, "get", boom)
        rates = fx.refresh_rates(store)  # must not raise
        assert rates == {"EUR": 1.1, "GBP": 1.3, "BRL": 0.2}  # retained, not clobbered
        assert store.get_fx_rates() == {"EUR": 1.1, "GBP": 1.3, "BRL": 0.2}
    finally:
        store.close()
