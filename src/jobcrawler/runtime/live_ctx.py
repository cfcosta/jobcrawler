"""The production live-Ctx factory (§4.5, §4.10).

A bare ``Ctx`` (no pacer, no robots, no solver) is right for the unit tests and
the offline validation sandbox, but a *production* committed crawl must be a good
citizen of the site it scrapes. This is the single seam that assembles the polite,
challenge-aware ``Ctx`` the daemon's crawls run through:

  * one ``httpx`` client — carrying the configured User-Agent, per-request timeout,
    and (when set) the egress proxy — shared by both the crawl fetches and the
    robots.txt fetch, so cf_clearance's IP+UA binding holds across them;
  * a **per-host pacer** (``JOBCRAWLER_RATE_LIMIT``) — always on;
  * a **robots.txt policy** — always on, fetching ``/robots.txt`` through that same
    client and failing open on an unreachable file;
  * a **challenge solver + clearance cache** — only when ``JOBCRAWLER_SOLVER`` names
    a backend (default ``none`` → plain httpx; the opt-in FlareSolverr backend is
    built by ``make_solver``).

The production crawl path (``runtime._collect_entry``) builds its ``Ctx`` here, so
every scheduled crawl is rate-limited and robots-compliant without the generated
crawler containing any of that logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Collection
from urllib.parse import urljoin

import httpx

from jobcrawler import config
from jobcrawler.runtime.ctx import Ctx
from jobcrawler.runtime.encoding import (
    http_charset_from_content_type,
    resolve_text,
)
from jobcrawler.runtime.politeness import (
    PerHostPacer,
    RobotsDisallowedError,
    RobotsPolicy,
)
from jobcrawler.runtime.solver import make_clearance_cache, make_solver
from jobcrawler.runtime.ssrf import check_url
from jobcrawler.urls import normalize_host


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


# §4.9 recheck redirect policy: statuses whose Location a recheck may follow
# (same-host only, bounded). The recheck is GET-only, so 303's "see other → GET"
# downgrade is a no-op here.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RECHECK_MAX_REDIRECTS = 5


def build_recheck_fetch(
    *, client: httpx.Client | None = None
) -> Callable[[str], Awaitable[tuple[int, str]]]:
    """A host-side polite GET for the §4.9 recheck sweep: ``(status, decoded text)``.

    The scheduler re-fetches an already-known job URL to learn whether it has quietly
    404/410'd or now shows an expired-posting marker (the caller reads status + body for
    the one-way closure decision). This shares the crawl's §4.5 politeness — the per-host
    pacer, the robots gate, the configured UA / timeout / egress proxy, and the SSRF
    guard — but deliberately drops the ``Ctx`` *page budget*: that cap is a per-crawl
    runaway bound, and a daemon-lifetime recheck fetch under a fixed page cap would
    exhaust it after a few sweeps and then refuse every later recheck. The pacer and
    robots caches are per-host and lifetime-safe (unlike the page counter), so a sweep of
    many URLs on one host costs a single robots fetch and paces exactly like a crawl.

    The SSRF floor is per-URL *own-host*, not a fixed fleet allowlist — matching the
    store's §4.9 ``_url_is_safe_to_store`` admission: a public job URL on a ``url_evidence``
    sibling host the crawler itself never fetched is still re-checkable, while any internal
    target (cloud-metadata / loopback / private / link-local) is refused. A robots-disallowed
    path likewise raises; the scheduler isolates every raise as "not a closure signal" and
    keeps the row open, so a blip or a refusal never ends a live posting.

    **Same-host** redirects are followed (bounded to ``_RECHECK_MAX_REDIRECTS`` hops):
    boards commonly locale-redirect every posting URL unconditionally (Coinbase 302s
    ``/careers/positions/<id>`` to ``/pt-br/...`` by egress geography), so stopping at
    the 3xx would blind the sweep to the real 200/404/expired body behind it and leave a
    windowed source's positions permanently unclosable. Each hop re-runs the SSRF check
    and the robots gate and is paced like any other request, so following never widens
    the guard. A **cross-host** redirect is still a moved posting — not a closure
    signal — so the 3xx itself is surfaced (the scheduler keeps the row open) rather
    than chasing an off-host target. ``client`` is injectable for tests (a
    MockTransport); production passes ``None`` and the factory builds the proxied,
    UA-stamped client, reused for the daemon's life (closed at process exit).
    """
    ua = config.user_agent()
    http = client or httpx.Client(
        headers={"user-agent": ua},
        follow_redirects=False,
        timeout=config.fetch_timeout(),
        proxy=config.egress_proxy(),  # None → direct
    )
    pacer = PerHostPacer(config.rate_limit())
    robots = RobotsPolicy(_own_host_robots_fetcher(http), user_agent=ua)

    async def fetch(url: str) -> tuple[int, str]:
        def _answer(resp: httpx.Response) -> tuple[int, str]:
            return resp.status_code, resolve_text(
                resp.content,
                http_charset_from_content_type(resp.headers.get("content-type")),
            )

        def _get() -> tuple[int, str]:
            origin_host = normalize_host(url)
            target = url
            hops_left = _RECHECK_MAX_REDIRECTS
            while True:
                # own host ok; internal target refused — re-run on EVERY hop so a
                # redirect can never route the sweep somewhere the guard refused.
                check_url(target, {normalize_host(target)})
                if not robots.allowed(target):
                    raise RobotsDisallowedError(target)
                # robots' Crawl-delay raises the floor; the pacer never speeds past
                # our rate — each hop paces like any other request.
                pacer.wait(
                    normalize_host(target), min_interval=robots.crawl_delay(target)
                )
                resp = http.get(target)
                location = resp.headers.get("location")
                if (
                    hops_left == 0
                    or resp.status_code not in _REDIRECT_STATUSES
                    or location is None
                ):
                    return _answer(resp)
                nxt = urljoin(target, location)
                if normalize_host(nxt) != origin_host:
                    # Cross-host move: not closure evidence — surface the 3xx itself.
                    return _answer(resp)
                hops_left -= 1
                target = nxt

        # httpx here is sync; run it off the scheduler's event loop so a paced/slow
        # recheck fetch never blocks the run loop's other cadences.
        return await asyncio.to_thread(_get)

    return fetch


def _own_host_robots_fetcher(client: httpx.Client) -> Callable[[str], str | None]:
    """robots.txt fetcher for the recheck path, SSRF-checked against the file's *own* host.

    Like :func:`_robots_fetcher` but scoped per-request to the requested host rather than a
    fixed crawl allowlist, so robots for any public job-URL host can be consulted (the
    recheck fetch is own-host, not fleet-scoped). Non-200 / unreachable → ``None`` so the
    policy fails open, per the robots convention.
    """

    def fetch_text(url: str) -> str | None:
        check_url(url, {normalize_host(url)})
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resolve_text(
            resp.content,
            http_charset_from_content_type(resp.headers.get("content-type")),
        )

    return fetch_text
