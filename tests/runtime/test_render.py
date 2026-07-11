"""Tests for the headless render tier (§4.10 / M2 Stage 1).

``render_page`` drives the operator's Chrome over CDP to fetch a JS-rendered DOM
and observe the runtime resource URLs the page fetches — the two signals
generation needs to escalate a static empty shell (settled HTML + the XHR/API a
static crawler can hit directly). Tests drive it with a fake browser (the
autofill duck-typed style), never a real Chrome.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from jobcrawler.cdp import CdpBrowser, CdpError
from jobcrawler.runtime.render import RenderResult, render_page


class _FakeBrowser:
    """Duck-typed CdpBrowser: dispatches evaluate() on the expression it's handed.

    ``ready`` is the queue of ``document.readyState`` answers (last repeats), so a
    test can model a page that is still ``loading`` for a poll or two before it
    goes ``complete``.
    """

    def __init__(
        self,
        *,
        html: str = "<html></html>",
        resources: list[str] | None = None,
        ready: list[str] | None = None,
    ) -> None:
        self.connected = False
        self.cmds: list[str] = []
        self.contexts_created = 0
        self.contexts_disposed: list[str] = []
        self.created_targets: list[dict[str, Any]] = []
        self.attached: list[str] = []
        self._html = html
        self._resources = resources or []
        self._ready = ready or ["complete"]

    async def connect(self) -> None:
        self.connected = True

    async def cmd(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self.cmds.append(method)
        if method == "Target.createBrowserContext":
            self.contexts_created += 1
            return {"browserContextId": "BC1"}
        if method == "Target.createTarget":
            self.created_targets.append(params or {})
            return {"targetId": "T1"}
        if method == "Target.disposeBrowserContext":
            self.contexts_disposed.append((params or {})["browserContextId"])
            return {}
        return {}

    async def attach(self, target_id: str) -> str:
        self.attached.append(target_id)
        return "S" + target_id

    async def evaluate(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = False,
        timeout: float | None = None,
    ) -> Any:
        if "readyState" in expression:
            return self._ready.pop(0) if len(self._ready) > 1 else self._ready[0]
        if "outerHTML" in expression:
            return self._html
        if "getEntriesByType" in expression:
            return list(self._resources)
        raise AssertionError(f"unexpected evaluate: {expression[:60]}")


def _render(browser: _FakeBrowser, url: str, **kw: Any) -> RenderResult:
    kw.setdefault("settle_s", 0.0)
    kw.setdefault("poll_s", 0.0)
    return asyncio.run(render_page(cast(CdpBrowser, browser), url, **kw))


def test_render_page_returns_settled_html_and_observed_resources() -> None:
    fake = _FakeBrowser(
        html="<html><body><div class='job'>Staff Engineer</div></body></html>",
        resources=[
            "https://boards.example.com/api/jobs.json",
            "https://cdn.example.com/app.js",
        ],
    )
    result = _render(fake, "https://example.com/careers")

    assert isinstance(result, RenderResult)
    assert "Staff Engineer" in result.html
    # the runtime XHR/API the SPA fired — the whole point of rendering
    assert "https://boards.example.com/api/jobs.json" in result.resource_urls
    assert fake.connected


def test_render_page_uses_and_disposes_an_isolated_context() -> None:
    # A throwaway browser context keeps generation's render off the operator's
    # cookies/session and is torn down even though nothing raised.
    fake = _FakeBrowser(html="<html></html>")
    _render(fake, "https://example.com/careers")

    assert fake.contexts_created == 1
    assert fake.contexts_disposed == ["BC1"]
    # the tab was opened inside that isolated context, not the default one
    assert fake.created_targets[0].get("browserContextId") == "BC1"


def test_render_page_polls_until_the_document_is_complete() -> None:
    # The DOM we snapshot must be the settled one: a page still ``loading`` is
    # polled until it reports ``complete``, not read on the first tick.
    fake = _FakeBrowser(
        html="<html><body>ready</body></html>",
        ready=["loading", "loading", "complete"],
    )
    result = _render(fake, "https://example.com/careers")

    assert "ready" in result.html
    assert fake._ready == ["complete"]  # both "loading" answers were consumed


def test_render_page_disposes_the_context_even_when_rendering_raises() -> None:
    # If the snapshot itself blows up, the isolated context must still be torn
    # down — otherwise a failed generation render leaks a Chrome context.
    class _Boom(_FakeBrowser):
        async def evaluate(self, session_id: str, expression: str, **kw: Any) -> Any:
            if "outerHTML" in expression:
                raise CdpError("page JS raised: boom")
            return await super().evaluate(session_id, expression, **kw)

    fake = _Boom()
    with pytest.raises(CdpError):
        _render(fake, "https://example.com/careers")

    assert fake.contexts_disposed == ["BC1"]
