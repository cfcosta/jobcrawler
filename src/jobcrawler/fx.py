"""Live currency→USD exchange rates for salary normalization (§4.6).

Host-side enrichment is a *pure, offline* function — no network on the crawl path — so
exchange rates can't be fetched inline while converting a salary. Instead they are pulled
**out-of-band** here (a daily daemon job, or ``jobcrawl fx-refresh`` on demand), persisted
to the ``fx_rates`` table, and read back by :meth:`StateStore.fx_table` for
:func:`usd_annual_band`. The static :data:`~jobcrawler.runtime.normalize.FX_TO_USD` stays
the floor: conversion keeps working before the first pull, and if a refresh fails the last
stored rates are retained (fail-soft — a skipped daily pull is harmless because the feed
only moves once a day and coarse ranking tolerates the drift).

Source: exchangerate-api.com's open endpoint (``open.er-api.com``), a keyless USD-based
JSON feed. It is fetched with a plain HTTP client — *not* through the crawl ``Ctx``, whose
SSRF allowlist would (correctly) reject an off-domain host — the same posture as
:mod:`jobcrawler.notify`. Attribution: powered by exchangerate-api.com.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from jobcrawler import config

if TYPE_CHECKING:
    from jobcrawler.state import StateStore

_log = logging.getLogger(__name__)

# The non-USD currencies the free-text detector emits (``_detect_currency``); a response
# missing any of them can't convert the salaries we actually parse, so treat it as a failed
# pull rather than silently narrowing coverage.
_REQUIRED_MAJORS = ("EUR", "GBP", "BRL")


def fetch_rates(url: str | None = None, *, timeout: float | None = None) -> dict[str, float]:
    """GET the FX endpoint and return ``{currency: USD-per-unit}``. Raises on any failure.

    The endpoint is USD-based — its ``rates`` are *units of X per 1 USD* — so each is
    **inverted** to the USD-per-unit factor :func:`usd_annual_band` multiplies by (``USD``
    is the identity). A non-numeric or non-positive quote is skipped; a response that isn't
    ``result: success``, carries no ``rates`` map, or is missing a required major raises —
    so a partial/garbage payload never overwrites good stored rates.
    """
    endpoint = url or config.fx_endpoint()
    resp = httpx.get(
        endpoint,
        timeout=timeout if timeout is not None else config.fetch_timeout(),
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()

    result = data.get("result") if isinstance(data, dict) else None
    if result is not None and result != "success":
        raise RuntimeError(f"fx: endpoint returned result={result!r}")
    rates = data.get("rates") if isinstance(data, dict) else None
    if not isinstance(rates, dict) or not rates:
        raise RuntimeError("fx: response carried no rates map")

    out: dict[str, float] = {}
    for code, per_usd in rates.items():
        if isinstance(per_usd, (int, float)) and not isinstance(per_usd, bool) and per_usd > 0:
            out[str(code).strip().upper()] = 1.0 / float(per_usd)
    out["USD"] = 1.0

    missing = [c for c in _REQUIRED_MAJORS if c not in out]
    if missing:
        raise RuntimeError(f"fx: response missing required majors {missing}")
    return out


def refresh_rates(store: StateStore, *, url: str | None = None) -> dict[str, float]:
    """Fetch live rates and persist them; on ANY failure keep the stored set (fail-soft).

    The daemon path: a fetch error, timeout, or bad payload is logged and swallowed so the
    scheduler loop is never interrupted and yesterday's rates stand until the next pull.
    Returns the rates now in effect — freshly written on success, or the retained set on
    failure (empty only if none were ever stored).
    """
    try:
        rates = fetch_rates(url)
    except Exception:
        _log.warning("fx: refresh failed; keeping previously stored rates", exc_info=True)
        return store.get_fx_rates()
    store.set_fx_rates(rates)
    _log.info("fx: refreshed %d exchange rates", len(rates))
    return rates
