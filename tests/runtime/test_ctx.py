"""Tests for the ctx fetch boundary (§4.5).

ctx is the single place fetching happens, so SSRF (§8), redirects, and
encoding are enforced centrally. Tests drive it with an httpx MockTransport —
no real network.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from datetime import timedelta

from jobcrawler.runtime.contract import Ctx as CtxProtocol
from jobcrawler.runtime.ctx import Ctx, PageLimitError, crawler_facade
from jobcrawler.runtime.politeness import RobotsDisallowedError
from jobcrawler.runtime.solver import (
    ChallengeError,
    ChallengeSolver,
    Clearance,
    ClearanceCache,
)
from jobcrawler.runtime.ssrf import SsrfError

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


# -- §4.5 transient-fetch retry with backoff + Retry-After --------------------


def _ctx_retry(
    handler: Handler,
    *,
    retries: int = 2,
    backoff: float = 0.5,
    retry_cap: float = 30.0,
) -> tuple[Ctx, list[float]]:
    """A bare Ctx with the network mocked and ``sleep`` captured, so retry/backoff
    timing is asserted without the test actually waiting."""
    waits: list[float] = []
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    ctx = Ctx(
        {"example.com"},
        client=client,
        retries=retries,
        backoff=backoff,
        retry_cap=retry_cap,
        sleep=waits.append,
    )
    return ctx, waits


def test_fetch_retries_a_transient_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503)  # a plain 503, not a Cloudflare challenge
        return httpx.Response(200, text="OK")

    ctx, waits = _ctx_retry(handler, retries=2, backoff=0.5)
    assert ctx.fetch("https://example.com/a") == "OK"
    assert calls["n"] == 3
    assert waits == [0.5, 1.0]  # exponential: 0.5·2⁰, then 0.5·2¹


def test_fetch_honors_retry_after_over_backoff() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, text="OK")

    ctx, waits = _ctx_retry(handler, retries=2, backoff=0.5)
    assert ctx.fetch("https://example.com/a") == "OK"
    assert waits == [7.0]  # the server's Retry-After wins over the 0.5 backoff


def test_fetch_gives_up_after_max_retries_and_raises() -> None:
    ctx, waits = _ctx_retry(lambda r: httpx.Response(503), retries=2, backoff=0.25)
    with pytest.raises(httpx.HTTPStatusError):
        ctx.fetch("https://example.com/a")
    assert waits == [0.25, 0.5]  # two backoffs, then the third attempt is surfaced


def test_fetch_retries_a_transient_transport_error() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset")
        return httpx.Response(200, text="OK")

    ctx, waits = _ctx_retry(handler, retries=2, backoff=0.5)
    assert ctx.fetch("https://example.com/a") == "OK"
    assert waits == [0.5]


def test_fetch_refuses_a_retry_after_beyond_the_cap() -> None:
    # A site asking us to wait an hour: surface the response rather than park the
    # crawl budget. We neither sleep nor retry — raise_for_status carries the 503.
    ctx, waits = _ctx_retry(
        lambda r: httpx.Response(503, headers={"retry-after": "3600"}),
        retries=2,
        retry_cap=30.0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        ctx.fetch("https://example.com/a")
    assert waits == []


def test_fetch_does_not_retry_a_cloudflare_challenge() -> None:
    # A 503 *challenge* is the solver's job (§4.10), not a blind transient retry —
    # hammering it would just burn the budget. With no solver it surfaces at once.
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, headers={"server": "cloudflare"}, text="Just a moment..."
        )

    ctx, waits = _ctx_retry(handler, retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        ctx.fetch("https://example.com/a")
    assert waits == []


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


# -- §8 method-general fetch: POST rides the same enforcement path ------------


def _recording_ctx(
    responder: Handler,
) -> tuple[Ctx, list[tuple[str, bytes]]]:
    """A ctx whose transport records (method, body) for every request."""
    seen: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.content))
        return responder(request)

    return _ctx(handler), seen


def test_fetch_post_sends_method_and_json_body() -> None:
    ctx, seen = _recording_ctx(lambda r: httpx.Response(200, text="OK"))
    out = ctx.fetch("https://example.com/api", method="post", json={"page": 1})
    assert out == "OK"
    method, body = seen[0]
    assert method == "POST"  # the lowercase verb is normalized
    assert json.loads(body) == {"page": 1}


def test_fetch_post_303_downgrades_to_a_body_less_get() -> None:
    # Browser convention: 301/302/303 turn the replay into GET and drop the body —
    # replaying the POST at the redirect target would double-submit the search.
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(303, headers={"location": "https://example.com/r"})
        return httpx.Response(200, text="RESULTS")

    ctx, seen = _recording_ctx(responder)
    out = ctx.fetch("https://example.com/search", method="POST", json={"q": "x"})
    assert out == "RESULTS"
    assert seen[0][0] == "POST"
    assert seen[1] == ("GET", b"")


def test_fetch_post_307_replays_method_and_body() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api":
            return httpx.Response(
                307, headers={"location": "https://example.com/api-v2"}
            )
        return httpx.Response(200, text="OK")

    ctx, seen = _recording_ctx(responder)
    assert ctx.fetch("https://example.com/api", method="POST", json={"q": 1}) == "OK"
    assert [method for method, _ in seen] == ["POST", "POST"]
    assert json.loads(seen[1][1]) == {"q": 1}


def test_fetch_post_rechecks_ssrf_on_every_redirect_hop() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"location": "https://evil.com/x"})

    ctx, seen = _recording_ctx(responder)
    with pytest.raises(SsrfError):
        ctx.fetch("https://example.com/api", method="POST", json={})
    assert len(seen) == 1  # the off-domain hop was never requested


def test_fetch_post_charges_the_page_budget() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
        follow_redirects=False,
    )
    ctx = Ctx({"example.com"}, client=client, max_pages=1)
    ctx.fetch("https://example.com/api", method="POST", json={})
    with pytest.raises(PageLimitError):
        ctx.fetch("https://example.com/api", method="POST", json={})


def test_fetch_post_retries_a_transient_429_with_the_body_replayed() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(429)
        return httpx.Response(200, text="OK")

    ctx, waits = _ctx_retry(handler, retries=2, backoff=0.5)
    assert ctx.fetch("https://example.com/api", method="POST", json={"p": 2}) == "OK"
    assert waits == [0.5]
    assert [json.loads(b) for b in bodies] == [{"p": 2}, {"p": 2}]


def test_fetch_caller_headers_survive_under_a_cached_clearance() -> None:
    # An API's mandatory Accept rides alongside the solved cookie (§4.10): both
    # must arrive — the clearance only wins an outright header collision.
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"] == "application/json"
        assert "cf_clearance" in request.headers.get("cookie", "")
        return httpx.Response(200, text="OK")

    cache = ClearanceCache(ttl=timedelta(minutes=30))
    cache.put("example.com", Clearance(cookies={"cf_clearance": "x"}, user_agent="UA"))
    ctx = _with_client(handler, clearance=cache)
    out = ctx.fetch("https://example.com/a", headers={"accept": "application/json"})
    assert out == "OK"


# -- capability facade (§8, #20) ---------------------------------------------


def test_facade_forwards_fetch_and_charges_the_real_budget() -> None:
    # A crawler calling facade.fetch rides the real ctx's guard and budget: the
    # body comes back and pages_fetched ticks on the ENFORCING ctx the host holds.
    ctx = _ctx(lambda req: httpx.Response(200, text="<ok/>"))
    facade = crawler_facade(ctx)

    body = facade.fetch("https://example.com/jobs")

    assert body == "<ok/>"
    assert ctx.pages_fetched == 1


def test_facade_forwards_ssrf_and_budget_enforcement() -> None:
    # Enforcement is not bypassed by the wrapper: an off-allowlist host still
    # raises through the facade exactly as it would on the bare ctx.
    facade = crawler_facade(_ctx(lambda req: httpx.Response(200), allowed={"example.com"}))
    with pytest.raises(SsrfError):
        facade.fetch("https://evil.test/x")


def test_facade_is_a_valid_ctx_but_hides_enforcement_state() -> None:
    ctx = _ctx(lambda req: httpx.Response(200, text=""))
    facade = crawler_facade(ctx)

    # It structurally satisfies the crawler contract...
    assert isinstance(facade, CtxProtocol)
    # ...yet exposes none of the single-underscore guard state a hostile crawler
    # would pivot through (the gate permits single-underscore access), nor a
    # raw-Response `get`, nor any `_ctx` handle back to the enforcing object.
    for attr in (
        "_client",
        "_allowed",
        "_max_pages",
        "_pacer",
        "_robots",
        "_ctx",
        "get",
    ):
        assert not hasattr(facade, attr)
    # __slots__ = () → no instance __dict__ to smuggle a reference through.
    with pytest.raises(TypeError):
        vars(facade)


def test_facade_only_reaches_ctx_through_a_gate_banned_dunder() -> None:
    # The lone handle to the enforcing ctx lives in the method closure — reachable
    # only via __closure__ (a dunder the AST gate rejects) and getattr (banned).
    # This pins WHY single-underscore access finds nothing to pivot through.
    ctx = _ctx(lambda req: httpx.Response(200, text=""))
    facade = crawler_facade(ctx)

    cells = facade.fetch.__func__.__closure__
    assert cells is not None
    assert any(cell.cell_contents is ctx for cell in cells)


# -- resolve: the tracking-hop primitive (§8, #130) ---------------------------


def test_resolve_returns_off_allowlist_target_without_requesting_it() -> None:
    # The whole point: the /z/ 302's Location is captured as DATA. The foreign
    # ATS host is never admitted, never dialed.
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "https://boards.greenhouse.io/acme/jobs/1"}
        )

    target = _ctx(handler).resolve("https://example.com/z/abc")

    assert target == "https://boards.greenhouse.io/acme/jobs/1"
    assert requested == ["https://example.com/z/abc"]


def test_resolve_follows_on_allowlist_hops_to_the_edge() -> None:
    # A tracker that chains through the site's own paths stays followed (each
    # hop re-checked + paced); the first hop that leaves the allowlist ends it.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/out":
            return httpx.Response(302, headers={"location": "/track/42"})
        if request.url.path == "/track/42":
            return httpx.Response(
                302, headers={"location": "https://jobs.lever.co/acme/1"}
            )
        raise AssertionError(f"unexpected request: {request.url}")

    assert (
        _ctx(handler).resolve("https://example.com/out")
        == "https://jobs.lever.co/acme/1"
    )


def test_resolve_of_a_non_redirect_returns_the_url_itself() -> None:
    # A chain that ends on-site (the bounce-back bot defense tripping) resolves
    # to the terminal on-site URL; the caller decides that is not a capture.
    ctx = _ctx(lambda r: httpx.Response(200, text="a page"))
    assert ctx.resolve("https://example.com/final") == "https://example.com/final"


def test_resolve_sends_caller_headers() -> None:
    # Referer is the whole trick for tracking hops: refuse-less-referer defenses
    # only reveal the destination when the linking page rides along.
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("referer")
        return httpx.Response(302, headers={"location": "https://ats.example.org/x"})

    _ctx(handler).resolve(
        "https://example.com/z/1", headers={"Referer": "https://example.com/i/t"}
    )
    assert seen["referer"] == "https://example.com/i/t"


def test_resolve_refuses_internal_redirect_targets() -> None:
    # An off-allowlist target is data, but an internal one is an attack: a
    # tracker pointing at loopback/metadata must not launder into stored URLs.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )

    with pytest.raises(SsrfError):
        _ctx(handler).resolve("https://example.com/z/1")


def test_resolve_rejects_off_allowlist_start_before_any_request() -> None:
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200)

    with pytest.raises(SsrfError):
        _ctx(handler).resolve("https://evil.test/x")
    assert requested == []


def test_resolve_charges_one_page_like_fetch() -> None:
    ctx = _ctx(
        lambda r: httpx.Response(
            302, headers={"location": "https://ats.example.org/x"}
        )
    )
    ctx.resolve("https://example.com/z/1")
    assert ctx.pages_fetched == 1
    with pytest.raises(PageLimitError):
        Ctx({"example.com"}, client=ctx._client, max_pages=0).resolve(
            "https://example.com/z/2"
        )


def test_resolve_raises_on_an_endless_on_allowlist_loop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    with pytest.raises(SsrfError):
        _ctx(handler).resolve("https://example.com/loop", max_redirects=3)


def test_resolve_raises_for_terminal_error_statuses() -> None:
    ctx = _ctx(lambda r: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        ctx.resolve("https://example.com/z/gone")


def test_facade_forwards_resolve() -> None:
    ctx = _ctx(
        lambda r: httpx.Response(
            302, headers={"location": "https://ats.example.org/x"}
        )
    )
    facade = crawler_facade(ctx)
    assert facade.resolve("https://example.com/z") == "https://ats.example.org/x"
    assert ctx.pages_fetched == 1
