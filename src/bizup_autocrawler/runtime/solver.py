"""Cloudflare / anti-bot challenge handling (§4.10).

Most news sites use Cloudflare only as a CDN (plain httpx works) and feed-first
discovery often sidesteps challenges, so escalation is **only on detection**.
When a real challenge appears, ``ctx`` resolves it transparently — generated
scripts never contain anti-bot logic.

This module owns the pluggable pieces: challenge *detection*, the
``ChallengeSolver`` interface (``solve(url) -> Clearance``), the per-host
clearance cache ("pay once": a solved ``cf_clearance`` + its UA are replayed with
cheap httpx until expiry), and the FlareSolverr backend. The in-process stealth
browser backends (nodriver / camoufox / patchright) need a real browser and are
not available in this environment — ``make_solver`` reports them unsupported
rather than pretending. cf_clearance is bound to IP+UA, so the solver and the
cheap follow-up requests must share egress IP, UA, and proxy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

import httpx

from bizup_autocrawler import config
from bizup_autocrawler.durations import parse_duration


class ChallengeError(Exception):
    """A Cloudflare challenge could not be cleared (no solver, or still blocked)."""


@dataclass(frozen=True, slots=True)
class Clearance:
    """A solved challenge: cookies to replay plus the bound User-Agent."""

    cookies: dict[str, str]
    user_agent: str

    def headers(self) -> dict[str, str]:
        """The cookie + User-Agent headers to replay this clearance."""
        out = {"cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items())}
        if self.user_agent:
            out["user-agent"] = self.user_agent
        return out


@runtime_checkable
class ChallengeSolver(Protocol):
    def solve(self, url: str) -> Clearance: ...


_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "turnstile",
    "cf_chl_opt",
)


def is_challenge(response: httpx.Response) -> bool:
    """True if ``response`` is a Cloudflare challenge, not a plain CDN response."""
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if response.status_code in (403, 503):
        server = response.headers.get("server", "").lower()
        if "cloudflare" in server:
            body = response.text.lower()
            return any(marker in body for marker in _CHALLENGE_MARKERS)
    return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ClearanceCache:
    """Per-host clearance cache with TTL expiry (the "pay once" replay)."""

    def __init__(
        self, *, ttl: timedelta, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._store: dict[str, tuple[Clearance, datetime]] = {}

    def get(self, host: str) -> Clearance | None:
        entry = self._store.get(host)
        if entry is None:
            return None
        clearance, expires_at = entry
        if self._clock() >= expires_at:
            del self._store[host]
            return None
        return clearance

    def put(self, host: str, clearance: Clearance) -> None:
        self._store[host] = (clearance, self._clock() + self._ttl)


class FlareSolverrSolver:
    """The FlareSolverr backend: an HTTP service driving a stealth browser."""

    def __init__(self, endpoint: str, *, client: httpx.Client | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0)

    def solve(self, url: str) -> Clearance:
        response = self._client.post(
            f"{self._endpoint}/v1",
            json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
        )
        response.raise_for_status()
        solution = response.json()["solution"]
        cookies = {c["name"]: c["value"] for c in solution.get("cookies", [])}
        return Clearance(cookies=cookies, user_agent=solution.get("userAgent", ""))


def make_solver() -> ChallengeSolver | None:
    """Build the configured solver backend, or ``None`` when challenges are off."""
    backend = config.solver().lower()
    if backend in ("none", "off", ""):
        return None
    if backend == "flaresolverr":
        url = config.flaresolverr_url()
        return FlareSolverrSolver(url) if url else None
    # stealth / nodriver / camoufox / patchright: need a real browser (not here).
    raise NotImplementedError(
        f"solver backend {backend!r} needs a stealth browser; set "
        "AUTOCRAWLER_SOLVER=flaresolverr with FLARESOLVERR_URL, or =none"
    )


def make_clearance_cache() -> ClearanceCache:
    return ClearanceCache(ttl=parse_duration(config.clearance_ttl()))
