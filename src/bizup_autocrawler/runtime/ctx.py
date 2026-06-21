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
from bizup_autocrawler.runtime.ssrf import SsrfError, check_url

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})


class Ctx:
    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        client: httpx.Client | None = None,
        user_agent: str | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._allowed = set(allowed_hosts)
        self._pages = 0
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
        """GET ``url`` (SSRF-checked per hop) and return decoded UTF-8 text."""
        self._pages += 1
        current = url
        for _ in range(max_redirects + 1):
            check_url(current, self._allowed)
            resp = self._client.get(current)
            if resp.status_code in _REDIRECT_CODES and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            resp.raise_for_status()
            return resp.text
        raise SsrfError(f"too many redirects from {url!r}")

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        check_url(url, self._allowed)
        self._pages += 1
        return self._client.get(url, **kwargs)

    def render(self, url: str) -> str:
        raise NotImplementedError(
            "headless rendering is the M2 Playwright tier (§4.10)"
        )

    def log(self, *args: object, **kwargs: object) -> None:
        print("[crawl]", *args)
