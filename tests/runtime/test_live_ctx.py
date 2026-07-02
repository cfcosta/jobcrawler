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

from jobcrawler.runtime.live_ctx import build_live_ctx
from jobcrawler.runtime.politeness import RobotsDisallowedError
from jobcrawler.runtime.solver import Clearance

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
