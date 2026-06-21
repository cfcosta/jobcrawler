"""The shared fetch boundary handed to every crawler (§4.5).

All fetching goes through ``ctx`` so SSRF (§8), redirects, encoding, timeouts
and (later) robots/rate-limit/Cloudflare are enforced centrally — the crawler
declares *what* to fetch, not *how*. ``fetch`` re-checks the SSRF guard on every
redirect hop (a redirect to an internal target would otherwise bypass it).
``render`` (the headless Playwright tier) and the politeness layer land in M2.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from urllib.parse import urljoin

import httpx

from bizup_autocrawler import config
from bizup_autocrawler.runtime.solver import (
    ChallengeError,
    ChallengeSolver,
    ClearanceCache,
    is_challenge,
)
from bizup_autocrawler.runtime.ssrf import SsrfError, check_url
from bizup_autocrawler.urls import normalize_host

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class Ctx:
    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        client: httpx.Client | None = None,
        user_agent: str | None = None,
        timeout: float = 20.0,
        solver: ChallengeSolver | None = None,
        clearance: ClearanceCache | None = None,
    ) -> None:
        self._allowed = set(allowed_hosts)
        self._pages = 0
        self._solver = solver
        self._clearance = clearance
        self._client = client or httpx.Client(
            headers={"user-agent": user_agent or config.user_agent()},
            follow_redirects=False,
            timeout=timeout,
        )

    @property
    def pages_fetched(self) -> int:
        """Logical pages fetched this run (one per fetch/get), for the run record."""
        return self._pages

    def fetch(self, url: str, *, max_redirects: int = 5) -> str:
        """GET ``url`` (SSRF-checked per hop) and return decoded UTF-8 text.

        On a detected Cloudflare challenge ``fetch`` escalates transparently to
        the solver tier and replays the cached clearance (§4.10) — crawler code
        stays oblivious.
        """
        self._pages += 1
        current = url
        for _ in range(max_redirects + 1):
            check_url(current, self._allowed)
            resp = self._challenge_aware_get(current)
            if resp.status_code in _REDIRECT_CODES and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            resp.raise_for_status()
            return resp.text
        raise SsrfError(f"too many redirects from {url!r}")

    def _challenge_aware_get(self, url: str) -> httpx.Response:
        host = normalize_host(url)
        cached = self._clearance.get(host) if self._clearance is not None else None
        resp = self._client.get(url, headers=cached.headers() if cached else {})
        if self._solver is None or not is_challenge(resp):
            return resp
        # "Pay once": solve, cache, replay. Still challenged → give up (§4.10).
        clearance = self._solver.solve(url)
        if self._clearance is not None:
            self._clearance.put(host, clearance)
        resp = self._client.get(url, headers=clearance.headers())
        if is_challenge(resp):
            raise ChallengeError(f"still challenged after solve: {url!r}")
        return resp

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        check_url(url, self._allowed)
        self._pages += 1
        # Never auto-follow: a redirect target is unchecked, so following one
        # (e.g. 302 → 169.254.169.254) would bypass the SSRF guard. The caller
        # re-fetches the Location through ctx, which re-checks it.
        kwargs["follow_redirects"] = False
        return self._client.get(url, **kwargs)

    def render(self, url: str) -> str:
        raise NotImplementedError(
            "headless rendering is the M2 Playwright tier (§4.10)"
        )

    def log(self, *args: object, **kwargs: object) -> None:
        print("[crawl]", *args)
