"""The production live-Ctx factory (§4.5, §4.10).

A bare ``Ctx`` (no pacer, no robots, no solver) is right for the unit tests and
the offline validation sandbox, but a *production* committed crawl must be a good
citizen of the site it scrapes. This is the single seam that assembles the polite,
challenge-aware ``Ctx`` the daemon's crawls run through:

  * one ``httpx`` client — carrying the configured User-Agent, per-request timeout,
    and (when set) the egress proxy — shared by both the crawl fetches and the
    robots.txt fetch, so cf_clearance's IP+UA binding holds across them;
  * a **per-host pacer** (``AUTOCRAWLER_RATE_LIMIT``) — always on;
  * a **robots.txt policy** — always on, fetching ``/robots.txt`` through that same
    client and failing open on an unreachable file;
  * a **challenge solver + clearance cache** — only when ``AUTOCRAWLER_SOLVER`` names
    a backend (default ``none`` → plain httpx; the opt-in FlareSolverr backend is
    built by ``make_solver``).

The production crawl path (``runtime._collect_entry``) builds its ``Ctx`` here, so
every scheduled crawl is rate-limited and robots-compliant without the generated
crawler containing any of that logic.
"""

from __future__ import annotations

from collections.abc import Callable, Collection

import httpx

from bizup_autocrawler import config
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.encoding import (
    http_charset_from_content_type,
    resolve_text,
)
from bizup_autocrawler.runtime.politeness import PerHostPacer, RobotsPolicy
from bizup_autocrawler.runtime.solver import make_clearance_cache, make_solver
from bizup_autocrawler.runtime.ssrf import check_url


def build_live_ctx(
    allowed_hosts: Collection[str], *, client: httpx.Client | None = None
) -> Ctx:
    """Assemble the polite, challenge-aware ``Ctx`` for a production crawl (§4.5).

    ``client`` is injectable for tests (a MockTransport); production passes ``None``
    and the factory builds the proxied, UA-stamped client from config. The pacer and
    robots policy are always attached; the solver + clearance cache only when a
    backend is configured (``make_solver`` returns ``None`` for the default).
    """
    ua = config.user_agent()
    http = client or httpx.Client(
        headers={"user-agent": ua},
        follow_redirects=False,
        timeout=config.fetch_timeout(),
        proxy=config.egress_proxy(),  # None → direct
    )
    solver = make_solver()
    # A clearance cache is only meaningful alongside a solver (it stores what the
    # solver produces), so build it only when challenges are actually handled.
    clearance = make_clearance_cache() if solver is not None else None
    pacer = PerHostPacer(config.rate_limit())
    robots = RobotsPolicy(_robots_fetcher(http, allowed_hosts), user_agent=ua)
    return Ctx(
        allowed_hosts,
        client=http,
        solver=solver,
        clearance=clearance,
        pacer=pacer,
        robots=robots,
    )


def _robots_fetcher(
    client: httpx.Client, allowed_hosts: Collection[str]
) -> Callable[[str], str | None]:
    """A minimal robots.txt fetcher for :class:`RobotsPolicy` over the shared client.

    SSRF-checked like every other fetch, but it deliberately does *not* go through
    ``ctx`` — robots gating must not recurse into fetching robots.txt, and the robots
    request is neither paced nor charged against the page budget. Any error or non-200
    yields ``None`` so the policy fails open (the documented robots convention).
    """
    allowed = set(allowed_hosts)

    def fetch_text(url: str) -> str | None:
        check_url(
            url, allowed
        )  # SSRF guard; RobotsPolicy._load folds a raise → fail-open
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resolve_text(
            resp.content,
            http_charset_from_content_type(resp.headers.get("content-type")),
        )

    return fetch_text
