"""Headless render tier (§4.10 / M2 Stage 1): drive the operator's Chrome to
fetch a JS-rendered DOM and observe the runtime resources it fetches.

The payoff is generation-time escalation. Most boards a static fetch can't read
are SPAs: the homepage HTML is an empty shell and the postings arrive over XHR
from a JSON API. Rendering that shell in a real browser yields two things a
static crawler can use — the *settled* DOM, and the list of URLs the page
actually fetched at runtime — so the RLM can be pointed at the JSON API host the
committed (static) crawler will hit directly, rather than at a DOM it can never
reproduce.

Runs in the PARENT (generation), never inside a committed crawl: CDP to
``127.0.0.1:9222`` is blocked twice over inside the sandbox (the #19b seccomp
egress broker denies the ``connect`` and the allowlist proxy screens loopback).
Stage 1 is therefore a generation-time *observation* tool; production sandbox
render is deferred (Stage 2) behind its own egress plan.

The work is async (CDP is), but its callers are synchronous code already running
inside another event loop — generation runs :func:`prefetch_corpus` on the loop
thread, and a crawler's ``ctx.render`` is a plain method. :func:`render_page` is
the pure async choreography (unit-tested directly with a fake browser);
:class:`RenderBridge` is the sync front door that runs it on a dedicated
background loop so neither caller has to nest ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from jobcrawler.cdp import CdpBrowser, CdpError


@dataclass(frozen=True)
class RenderResult:
    """What one headless render observed: the settled DOM and the URLs the page
    fetched at runtime (its XHR/API/asset calls, for §6 API discovery)."""

    html: str
    resource_urls: tuple[str, ...] = ()


class RenderProvider(Protocol):
    """The render seam :class:`~jobcrawler.runtime.ctx.Ctx` depends on — declared
    structurally so :class:`RenderBridge` (or a test fake) satisfies it without an
    import edge back into ctx."""

    def render(self, url: str) -> RenderResult: ...


class _Browser(Protocol):
    """The slice of :class:`~jobcrawler.cdp.CdpBrowser` render drives — declared
    structurally so a test fake (or any duck-typed driver) satisfies it."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def cmd(
        self,
        method: str,
        params: dict[str, Any] | None = ...,
        *,
        session_id: str | None = ...,
        timeout: float | None = ...,
    ) -> dict[str, Any]: ...

    async def attach(self, target_id: str) -> str: ...

    async def evaluate(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = ...,
        timeout: float | None = ...,
    ) -> Any: ...


# The runtime resources a rendered page fetched, newest CDP semantics: the
# Performance Resource Timing entries' names are absolute URLs.
_RESOURCES_JS = "performance.getEntriesByType('resource').map(e => e.name)"
_OUTER_HTML_JS = "document.documentElement.outerHTML"
_READY_JS = "document.readyState"


async def render_page(
    browser: CdpBrowser | _Browser,
    url: str,
    *,
    settle_s: float = 0.5,
    poll_s: float = 0.1,
    timeout_s: float = 20.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RenderResult:
    """Render ``url`` in an isolated Chrome context and return its settled DOM +
    observed runtime resource URLs.

    The tab is created inside a throwaway ``Target.createBrowserContext`` (an
    incognito-like partition) so generation's render never rides the operator's
    logged-in cookies/session and leaves no state behind — the context (and its
    tab) is disposed in a ``finally``, even on error. After the document reaches
    ``readyState === 'complete'`` we wait ``settle_s`` for the post-load XHR that
    an SPA renders from, then snapshot ``outerHTML`` and the resource list.
    """
    await browser.connect()
    created = await browser.cmd(
        "Target.createBrowserContext", {"disposeOnDetach": True}
    )
    context_id = str(created["browserContextId"])
    try:
        target = await browser.cmd(
            "Target.createTarget",
            {"url": url, "browserContextId": context_id},
        )
        session_id = await browser.attach(str(target["targetId"]))
        await _await_document_ready(
            browser, session_id, poll_s=poll_s, timeout_s=timeout_s, sleep=sleep
        )
        # Let the SPA's post-load fetch/render settle before we snapshot.
        if settle_s > 0:
            await sleep(settle_s)
        html = await browser.evaluate(session_id, _OUTER_HTML_JS)
        resources = await browser.evaluate(session_id, _RESOURCES_JS)
        return RenderResult(
            html=str(html or ""),
            resource_urls=tuple(
                str(u) for u in (resources or []) if isinstance(u, str) and u
            ),
        )
    finally:
        # Best-effort teardown: a disposer failure must not mask a real error
        # (or a good result) from the body above.
        try:
            await browser.cmd(
                "Target.disposeBrowserContext", {"browserContextId": context_id}
            )
        except CdpError:
            pass


async def _await_document_ready(
    browser: CdpBrowser | _Browser,
    session_id: str,
    *,
    poll_s: float,
    timeout_s: float,
    sleep: Callable[[float], Awaitable[None]],
) -> None:
    """Poll ``document.readyState`` until ``complete`` or the deadline.

    Polling (rather than subscribing to ``Page.loadEventFired``) matches
    :mod:`jobcrawler.cdp`, which drops CDP events and only awaits command
    replies. A timeout is not an error — a page that never reports ``complete``
    still has a DOM worth snapshotting, so we fall through and let the caller
    read whatever rendered.
    """
    waited = 0.0
    while True:
        try:
            state = await browser.evaluate(session_id, _READY_JS)
        except CdpError:
            return  # navigation tore down the context we were polling — snapshot anyway
        if state == "complete":
            return
        if waited >= timeout_s:
            return
        await sleep(poll_s)
        waited += poll_s if poll_s > 0 else timeout_s  # poll_s==0 (tests): one pass


class RenderBridge:
    """Synchronous front door to :func:`render_page` for callers inside a loop.

    Generation drives rendering from synchronous code that is itself running on
    an event loop (``prefetch_corpus`` executes on the generation loop thread; a
    crawler's ``ctx.render`` is a plain method). Nesting ``asyncio.run`` there
    raises, and scheduling onto the *caller's* loop would deadlock (the caller is
    blocked waiting for the result). So the bridge owns a dedicated daemon thread
    running its own event loop; :meth:`render` submits the coroutine there with
    ``run_coroutine_threadsafe`` and blocks on the reply.

    One long-lived :class:`CdpBrowser` lives on that loop and is reused across
    calls, so Chrome's "Allow debugging" consent fires once per process, matching
    the §18 autofill drain's single-browser design.
    """

    def __init__(
        self,
        browser: CdpBrowser | _Browser | None = None,
        *,
        settle_s: float = 0.5,
        timeout_s: float = 20.0,
    ) -> None:
        self._browser = browser
        self._settle_s = settle_s
        self._timeout_s = timeout_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="jobcrawler-render", daemon=True
            )
            thread.start()
            self._loop, self._thread = loop, thread
            return loop

    def render(self, url: str) -> RenderResult:
        loop = self._ensure_loop()
        if self._browser is None:
            self._browser = CdpBrowser()
        fut = asyncio.run_coroutine_threadsafe(
            render_page(
                self._browser,
                url,
                settle_s=self._settle_s,
                timeout_s=self._timeout_s,
            ),
            loop,
        )
        # The render's own timeout_s bounds the async work; add a margin so a
        # wholly unresponsive browser can't wedge the caller forever.
        return fut.result(timeout=self._timeout_s + 30.0)

    def close(self) -> None:
        """Tear down the background loop + browser; safe to call more than once."""
        with self._lock:
            loop, self._loop = self._loop, None
            thread, self._thread = self._thread, None
            browser, self._browser = self._browser, None
        if loop is None:
            return

        async def _shutdown() -> None:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001 - teardown is best-effort
                    pass

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(timeout=10.0)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=5.0)


_shared_bridge: RenderBridge | None = None
_shared_bridge_lock = threading.Lock()


def shared_render_bridge() -> RenderBridge:
    """The process-wide render bridge generation escalation provisions.

    One per process — like the §18 autofill drain's single :class:`CdpBrowser` —
    so Chrome's "Allow debugging" consent fires once and only one background loop
    thread ever exists, no matter how many boards escalate. Lazily built and never
    torn down: it lives for the daemon's lifetime. Concurrent generations (the §6
    fan-out runs on ``to_thread`` workers) share it safely — renders multiplex
    over the one CDP connection, each in its own isolated browser context.
    """
    global _shared_bridge
    with _shared_bridge_lock:
        if _shared_bridge is None:
            _shared_bridge = RenderBridge()
        return _shared_bridge
