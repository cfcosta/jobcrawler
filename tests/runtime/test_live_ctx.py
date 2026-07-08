"""Tests for the production live-Ctx factory (§4.5, §4.10).

``build_live_ctx`` assembles the polite, challenge-aware ``Ctx`` the daemon's
committed crawls run through. The fetch path itself is covered in ``test_ctx``;
here we assert the *wiring* — that a real crawl through the factory is
robots-gated and paced by default, that challenges are not solved unless a solver
is configured, and that the egress proxy is honored. A MockTransport stands in
for the network (the same client is shared by the crawl path and the robots
fetch, mirroring production).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from pytest import MonkeyPatch

from jobcrawler.runtime.live_ctx import build_live_ctx, build_recheck_fetch
from jobcrawler.runtime.politeness import RobotsDisallowedError
from jobcrawler.runtime.solver import Clearance
from jobcrawler.runtime.ssrf import SsrfError

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_build_live_ctx_is_robots_gated_and_paced_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    # The release-safety guarantee: a production crawl is polite out of the box, with
    # no extra config. robots.txt is fetched through the same client and enforced — a
    # Disallow path raises before any request — and the per-host pacer is attached.
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")  # keep the test fast

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="body")

    ctx = build_live_ctx({"jobs.example"}, client=_client(handler))

    assert ctx.fetch("https://jobs.example/ok") == "body"
    with pytest.raises(RobotsDisallowedError):
        ctx.fetch("https://jobs.example/private")


def test_build_live_ctx_does_not_solve_challenges_by_default(
    monkeypatch: MonkeyPatch,
) -> None:
    # With no solver configured (the default), a Cloudflare challenge is left as-is —
    # the factory must not silently enable a solver. The challenge response surfaces
    # (raise_for_status on the 503) rather than being transparently cleared.
    monkeypatch.delenv("JOBCRAWLER_SOLVER", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            503, headers={"server": "cloudflare"}, text="Just a moment..."
        )

    ctx = build_live_ctx({"jobs.example"}, client=_client(handler))

    with pytest.raises(httpx.HTTPStatusError):
        ctx.fetch("https://jobs.example/x")


def test_build_live_ctx_clears_challenges_through_the_configured_solver(
    monkeypatch: MonkeyPatch,
) -> None:
    # When a solver is configured the factory threads it AND a clearance cache into
    # the Ctx, so a challenge is solved once and the cleared response replayed.
    # (make_solver's backend gating is tested in test_solver; here we assert
    # build_live_ctx wires whatever solver make_solver returns.)
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")
    solved = {"n": 0}

    class _FakeSolver:
        def solve(self, url: str) -> Clearance:
            solved["n"] += 1
            return Clearance(cookies={"cf_clearance": "x"}, user_agent="UA")

    monkeypatch.setattr(
        "jobcrawler.runtime.live_ctx.make_solver", lambda: _FakeSolver()
    )

    state = {"cleared": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if not state["cleared"]:
            state["cleared"] = True
            return httpx.Response(
                503, headers={"server": "cloudflare"}, text="just a moment"
            )
        return httpx.Response(200, text="cleared body")

    ctx = build_live_ctx({"jobs.example"}, client=_client(handler))

    assert ctx.fetch("https://jobs.example/x") == "cleared body"
    assert solved["n"] == 1  # solved once, then replayed from the clearance cache


def test_build_live_ctx_routes_through_the_egress_proxy(
    monkeypatch: MonkeyPatch,
) -> None:
    # cf_clearance binds to egress IP, so the live client must honor JOBCRAWLER_EGRESS_PROXY.
    monkeypatch.setenv("JOBCRAWLER_EGRESS_PROXY", "http://egress.local:8080")
    seen: dict[str, object] = {}
    real_client = httpx.Client

    def spy_client(**kwargs: object) -> httpx.Client:
        seen.update(kwargs)  # the factory builds its client with keyword args only
        return real_client()  # the test inspects the captured kwargs, not this client

    monkeypatch.setattr("jobcrawler.runtime.live_ctx.httpx.Client", spy_client)

    build_live_ctx({"jobs.example"})  # builds its own (proxied) client

    assert seen.get("proxy") == "http://egress.local:8080"


# -- recheck fetch (§4.9) ----------------------------------------------------


async def test_build_recheck_fetch_surfaces_status_and_body(
    monkeypatch: MonkeyPatch,
) -> None:
    # The §4.9 recheck seam returns (status, text) for a known job URL — and unlike a
    # crawl fetch it must SURFACE a 404 (the closure signal) rather than raise on it, so
    # recheck_tick can read the status and close the row.
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")  # keep the test fast

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)  # no robots → fail open (allow)
        if request.url.path == "/gone":
            return httpx.Response(404, text="not found")
        return httpx.Response(200, text="still open")

    fetch = build_recheck_fetch(client=_client(handler))

    assert await fetch("https://jobs.example/open") == (200, "still open")
    assert await fetch("https://jobs.example/gone") == (404, "not found")


async def test_build_recheck_fetch_is_robots_gated(monkeypatch: MonkeyPatch) -> None:
    # Mirrors the crawl fetch path: a robots Disallow refuses the recheck before any
    # request (the scheduler isolates the raise as "not a closure" and keeps the row).
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="body")

    fetch = build_recheck_fetch(client=_client(handler))

    assert await fetch("https://jobs.example/ok") == (200, "body")
    with pytest.raises(RobotsDisallowedError):
        await fetch("https://jobs.example/private")


async def test_build_recheck_fetch_refuses_internal_targets() -> None:
    # A drifted or hostile stored URL must not turn the recheck into an SSRF probe: the
    # own-host floor still blocks cloud-metadata / loopback / private / link-local, even
    # though the URL's own host is (trivially) allowed.
    fetch = build_recheck_fetch(client=_client(lambda _r: httpx.Response(200)))

    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/admin",
        "http://metadata.google.internal/",
    ):
        with pytest.raises(SsrfError):
            await fetch(url)


async def test_build_recheck_fetch_follows_same_host_redirects(
    monkeypatch: MonkeyPatch,
) -> None:
    # Boards commonly locale-redirect EVERY posting URL unconditionally (Coinbase 302s
    # /careers/positions/<id> to /pt-br/... by egress geography). Stopping at the 3xx
    # would blind the sweep to the real 200/404 behind it — the terminal answer (here
    # the 404 closure signal) must come back, not the redirect.
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")  # keep the test fast

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)  # no robots → fail open (allow)
        if request.url.path == "/careers/positions/1":
            return httpx.Response(
                302, headers={"location": "/pt-br/careers/positions/1"}
            )
        if request.url.path == "/pt-br/careers/positions/1":
            return httpx.Response(404, text="position closed")
        return httpx.Response(200, text="unexpected")

    fetch = build_recheck_fetch(client=_client(handler))

    assert await fetch("https://jobs.example/careers/positions/1") == (
        404,
        "position closed",
    )


async def test_build_recheck_fetch_surfaces_cross_host_redirects(
    monkeypatch: MonkeyPatch,
) -> None:
    # A cross-host redirect is a MOVED posting — not closure evidence — so the 3xx
    # itself surfaces (the scheduler keeps the row open) and the off-host target is
    # never fetched (an unchecked chase would widen the SSRF floor).
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://elsewhere.example/x"})

    fetch = build_recheck_fetch(client=_client(handler))

    status, _ = await fetch("https://jobs.example/job/1")
    assert status == 302
    assert set(hosts) == {"jobs.example"}  # the off-host target was never fetched


async def test_build_recheck_fetch_bounds_a_redirect_loop(
    monkeypatch: MonkeyPatch,
) -> None:
    # A same-host redirect loop must terminate at the hop bound and surface the 3xx
    # (keep-open) rather than spin forever or raise.
    monkeypatch.setenv("JOBCRAWLER_RATE_LIMIT", "1000/s/host")
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        gets += 1
        return httpx.Response(302, headers={"location": "/loop"})

    fetch = build_recheck_fetch(client=_client(handler))

    status, _ = await fetch("https://jobs.example/loop")
    assert status == 302
    assert gets == 6  # the initial GET + 5 followed hops, then surfaced
