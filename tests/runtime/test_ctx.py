"""Tests for the ctx fetch boundary (§4.5).

ctx is the single place fetching happens, so SSRF (§8), redirects, and
encoding are enforced centrally. Tests drive it with an httpx MockTransport —
no real network.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from datetime import timedelta

from bizup_autocrawler.runtime.contract import Ctx as CtxProtocol
from bizup_autocrawler.runtime.ctx import Ctx, PageLimitError
from bizup_autocrawler.runtime.politeness import RobotsDisallowedError
from bizup_autocrawler.runtime.solver import (
    ChallengeError,
    ChallengeSolver,
    Clearance,
    ClearanceCache,
)
from bizup_autocrawler.runtime.ssrf import SsrfError

Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(handler: Handler, allowed: set[str] | None = None) -> Ctx:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return Ctx(allowed or {"example.com"}, client=client)


class _FakeSolver:
    def __init__(self) -> None:
        self.calls = 0

    def solve(self, url: str) -> Clearance:
        self.calls += 1
        return Clearance(cookies={"cf_clearance": "x"}, user_agent="StealthUA")


class _ExplodingSolver:
    def solve(self, url: str) -> Clearance:
        raise AssertionError("solver must not be called when clearance is cached")


def test_satisfies_ctx_protocol() -> None:
    assert isinstance(_ctx(lambda r: httpx.Response(200, text="x")), CtxProtocol)


def test_fetch_returns_decoded_text() -> None:
    ctx = _ctx(lambda r: httpx.Response(200, text="hello"))
    assert ctx.fetch("https://example.com/a") == "hello"


def test_fetch_resolves_declared_charset() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content="café".encode("latin-1"),
            headers={"content-type": "text/html; charset=latin-1"},
        )

    assert ctx_text(_ctx(handler)) == "café"


def test_fetch_resolves_encoding_when_no_charset_header() -> None:
    # No HTTP charset: the §4.5 ladder must fall through to the document's <meta>
    # so a legacy page is handed over decoded, not as mojibake.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content='<meta charset="iso-8859-1"><p>Café</p>'.encode("latin-1"),
            headers={"content-type": "text/html"},  # charset absent on purpose
        )

    assert ctx_text(_ctx(handler)) == '<meta charset="iso-8859-1"><p>Café</p>'


def ctx_text(ctx: Ctx) -> str:
    return ctx.fetch("https://example.com/a")


def test_fetch_rejects_off_domain_before_any_request() -> None:
    ctx = _ctx(lambda r: httpx.Response(200, text="should not happen"))
    with pytest.raises(SsrfError):
        ctx.fetch("https://evil.com/x")


def test_fetch_follows_on_domain_redirect_with_recheck() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r":
            return httpx.Response(
                302, headers={"location": "https://example.com/final"}
            )
        return httpx.Response(200, text="FINAL")

    assert _ctx(handler).fetch("https://example.com/r") == "FINAL"


def test_fetch_blocks_redirect_to_off_domain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.com/x"})

    with pytest.raises(SsrfError):
        _ctx(handler).fetch("https://example.com/r")


def test_fetch_raises_on_too_many_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    with pytest.raises(SsrfError):
        _ctx(handler).fetch("https://example.com/loop", max_redirects=3)


def test_fetch_raises_for_status_errors() -> None:
    ctx = _ctx(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        ctx.fetch("https://example.com/missing")


def test_render_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        _ctx(lambda r: httpx.Response(200)).render("https://example.com/a")


# -- §4.10 Cloudflare challenge escalation -----------------------------------


def _challenge_then_clear(request: httpx.Request) -> httpx.Response:
    if "cf_clearance" in request.headers.get("cookie", ""):
        return httpx.Response(200, text="CLEARED")
    return httpx.Response(403, headers={"cf-mitigated": "challenge"})


def _with_client(
    handler: Handler,
    *,
    solver: ChallengeSolver | None = None,
    clearance: ClearanceCache | None = None,
) -> Ctx:
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return Ctx({"example.com"}, client=client, solver=solver, clearance=clearance)


def test_fetch_solves_challenge_and_retries() -> None:
    solver = _FakeSolver()
    ctx = _with_client(_challenge_then_clear, solver=solver)
    assert ctx.fetch("https://example.com/a") == "CLEARED"
    assert solver.calls == 1


def test_fetch_raises_when_still_challenged_after_solve() -> None:
    always = lambda r: httpx.Response(403, headers={"cf-mitigated": "challenge"})  # noqa: E731
    ctx = _with_client(always, solver=_FakeSolver())
    with pytest.raises(ChallengeError):
        ctx.fetch("https://example.com/a")


def test_cached_clearance_replayed_without_solving() -> None:
    cache = ClearanceCache(ttl=timedelta(minutes=30))
    cache.put("example.com", Clearance(cookies={"cf_clearance": "x"}, user_agent="UA"))
    ctx = _with_client(
        _challenge_then_clear, solver=_ExplodingSolver(), clearance=cache
    )
    assert ctx.fetch("https://example.com/a") == "CLEARED"  # cookie from cache


def test_no_solver_means_no_escalation() -> None:
    challenge = lambda r: httpx.Response(403, headers={"cf-mitigated": "challenge"})  # noqa: E731
    with pytest.raises(httpx.HTTPStatusError):
        _ctx(challenge).fetch("https://example.com/a")


def test_get_never_follows_a_redirect_to_an_internal_target() -> None:
    # SSRF: a crawler could pass follow_redirects=True; ctx.get must refuse so a
    # 302 to a metadata IP is never auto-fetched (the Location is unchecked).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(
                302, headers={"location": "http://169.254.169.254/meta"}
            )
        return httpx.Response(200, text="INTERNAL SECRET")

    resp = _with_client(handler).get("https://example.com/x", follow_redirects=True)
    assert resp.status_code == 302  # not followed
    assert "INTERNAL" not in resp.text


# -- §4.5 politeness: per-host pacing ----------------------------------------


class _RecordingPacer:
    """Captures (host, min_interval) so the ctx→pacer wiring is asserted directly."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float | None]] = []

    def wait(self, host: str, *, min_interval: float | None = None) -> None:
        self.calls.append((host, min_interval))


def test_fetch_paces_through_the_injected_pacer() -> None:
    pacer = _RecordingPacer()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x")),
        follow_redirects=False,
    )
    ctx = Ctx({"example.com"}, client=client, pacer=pacer)
    ctx.fetch("https://example.com/a")
    assert pacer.calls == [("example.com", None)]


def test_fetch_paces_each_redirect_hop() -> None:
    # Each hop is a real request to a host, so each must be paced — not just the first.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r":
            return httpx.Response(
                302, headers={"location": "https://example.com/final"}
            )
        return httpx.Response(200, text="FINAL")

    pacer = _RecordingPacer()
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    ctx = Ctx({"example.com"}, client=client, pacer=pacer)
    ctx.fetch("https://example.com/r")
    assert pacer.calls == [("example.com", None), ("example.com", None)]


def test_get_paces_through_the_injected_pacer() -> None:
    pacer = _RecordingPacer()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x")),
        follow_redirects=False,
    )
    ctx = Ctx({"example.com"}, client=client, pacer=pacer)
    ctx.get("https://example.com/a")
    assert pacer.calls == [("example.com", None)]


# -- §4.5 politeness: robots.txt ---------------------------------------------


class _FakeRobots:
    """A robots policy with canned verdicts so ctx enforcement is asserted directly."""

    def __init__(self, *, allow: bool = True, delay: float | None = None) -> None:
        self._allow = allow
        self._delay = delay

    def allowed(self, url: str) -> bool:
        return self._allow

    def crawl_delay(self, url: str) -> float | None:
        return self._delay


def _boom_client() -> httpx.Client:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("a robots-disallowed URL must never hit the network")

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_blocked_by_robots_raises_before_any_request() -> None:
    ctx = Ctx({"example.com"}, client=_boom_client(), robots=_FakeRobots(allow=False))
    with pytest.raises(RobotsDisallowedError):
        ctx.fetch("https://example.com/private")


def test_get_blocked_by_robots_raises_before_any_request() -> None:
    ctx = Ctx({"example.com"}, client=_boom_client(), robots=_FakeRobots(allow=False))
    with pytest.raises(RobotsDisallowedError):
        ctx.get("https://example.com/private")


def test_fetch_feeds_robots_crawl_delay_to_the_pacer() -> None:
    # A Crawl-delay raises this host's pacing floor — ctx passes it through as the
    # pacer's min_interval so robots can slow us below the configured base rate.
    pacer = _RecordingPacer()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="x")),
        follow_redirects=False,
    )
    ctx = Ctx(
        {"example.com"},
        client=client,
        pacer=pacer,
        robots=_FakeRobots(allow=True, delay=5.0),
    )
    ctx.fetch("https://example.com/a")
    assert pacer.calls == [("example.com", 5.0)]


def test_fetch_enforces_the_page_budget() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=False,
    )
    ctx = Ctx({"example.com"}, client=client, max_pages=2)
    ctx.fetch("https://example.com/1")
    ctx.fetch("https://example.com/2")
    with pytest.raises(PageLimitError):
        ctx.fetch("https://example.com/3")
