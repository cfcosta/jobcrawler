"""Tests for Cloudflare challenge handling (§4.10)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from bizup_autocrawler.runtime.solver import (
    ChallengeSolver,
    Clearance,
    ClearanceCache,
    FlareSolverrSolver,
    is_challenge,
)

UTC = timezone.utc


def test_detects_cf_mitigated_header() -> None:
    resp = httpx.Response(403, headers={"cf-mitigated": "challenge"})
    assert is_challenge(resp)


def test_detects_interstitial_body() -> None:
    resp = httpx.Response(
        503,
        headers={"server": "cloudflare"},
        text="<title>Just a moment...</title>",
    )
    assert is_challenge(resp)


def test_plain_cloudflare_cdn_response_is_not_a_challenge() -> None:
    assert not is_challenge(httpx.Response(200, headers={"server": "cloudflare"}))


def test_non_cloudflare_403_is_not_a_challenge() -> None:
    assert not is_challenge(httpx.Response(403, headers={"server": "nginx"}))


def test_clearance_cache_round_trip() -> None:
    cache = ClearanceCache(ttl=timedelta(minutes=30))
    clearance = Clearance(cookies={"cf_clearance": "abc"}, user_agent="UA/1.0")
    cache.put("example.com", clearance)
    assert cache.get("example.com") == clearance
    assert cache.get("other.com") is None


def test_clearance_cache_expires() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = lambda: now  # noqa: E731 - frozen clock for the test
    cache = ClearanceCache(ttl=timedelta(minutes=30), clock=clock)
    cache.put("example.com", Clearance(cookies={}, user_agent="UA"))
    assert cache.get("example.com") is not None

    now = now + timedelta(minutes=31)
    assert cache.get("example.com") is None


def test_flaresolverr_solver_parses_solution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "solution": {
                    "userAgent": "Mozilla/5.0 stealth",
                    "cookies": [
                        {"name": "cf_clearance", "value": "xyz"},
                        {"name": "other", "value": "1"},
                    ],
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    solver = FlareSolverrSolver("http://flare.test", client=client)
    clearance = solver.solve("https://example.com")

    assert isinstance(solver, ChallengeSolver)
    assert clearance.user_agent == "Mozilla/5.0 stealth"
    assert clearance.cookies["cf_clearance"] == "xyz"


def test_flaresolverr_solver_raises_on_error() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    solver = FlareSolverrSolver("http://flare.test", client=client)
    with pytest.raises(httpx.HTTPStatusError):
        solver.solve("https://example.com")
