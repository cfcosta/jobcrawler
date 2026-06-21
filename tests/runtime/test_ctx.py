"""Tests for the ctx fetch boundary (§4.5).

ctx is the single place fetching happens, so SSRF (§8), redirects, and
encoding are enforced centrally. Tests drive it with an httpx MockTransport —
no real network.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from bizup_autocrawler.runtime.contract import Ctx as CtxProtocol
from bizup_autocrawler.runtime.ctx import Ctx
from bizup_autocrawler.runtime.ssrf import SsrfError

Handler = Callable[[httpx.Request], httpx.Response]


def _ctx(handler: Handler, allowed: set[str] | None = None) -> Ctx:
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    return Ctx(allowed or {"example.com"}, client=client)


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
