"""Minimal async Chrome DevTools Protocol client for the §18 auto-apply prefill.

Adapted from the operator's chrome-cdp tooling (raw CDP over one browser-endpoint
WebSocket with flattened sessions) — no Puppeteer, no separate browser process. It
attaches to the operator's OWN Chrome via the remote-debugging port (the
``chrome://inspect/#remote-debugging`` toggle, default 9222), so tabs open in their
real, logged-in profile; Chrome may show an "Allow debugging" prompt on the first
connection per browser session.

aiohttp is the transport: it is already in the environment as a litellm dependency
and ships a WebSocket client, where httpx has none and adding ``websockets`` would
mean a uv.lock regen for one feature.

Scope is deliberately tiny — connect, open/attach a tab, run commands, evaluate
JS — because the §18 worker polls page state via ``Runtime.evaluate`` rather than
subscribing to CDP events (simpler, and robust to missed events across reconnects).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

import aiohttp

_log = logging.getLogger("jobcrawler.cdp")


class CdpError(RuntimeError):
    """A CDP command failed, the page's JS raised, or Chrome is unreachable."""


class CdpBrowser:
    """One WebSocket to a Chrome browser endpoint; tab sessions multiplex over it.

    Long-lived by design: the §18 drain keeps a single instance across runs so
    Chrome's per-connection "Allow debugging" consent fires once per daemon
    lifetime, not once per prefill. ``connect()`` is idempotent and re-entrant
    after a drop — callers just ensure it before use.
    """

    def __init__(
        self, http_url: str = "http://127.0.0.1:9222", *, timeout: float = 30.0
    ) -> None:
        self._http_url = http_url.rstrip("/")
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Discover the browser WebSocket URL and attach; no-op when already live."""
        if self.connected:
            return
        await self.close()  # drop any half-dead ws/session state before rebuilding
        session = aiohttp.ClientSession()
        try:
            async with session.get(
                f"{self._http_url}/json/version",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                meta = await resp.json()
            ws_url = str(meta.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                raise CdpError(
                    f"no webSocketDebuggerUrl in {self._http_url}/json/version"
                )
            # max_msg_size=0 lifts aiohttp's 4 MiB frame cap — a DOM/AX payload on a
            # heavy ATS page exceeds it and would kill the connection mid-command.
            ws = await session.ws_connect(ws_url, max_msg_size=0)
        except CdpError:
            await session.close()
            raise
        except Exception as exc:
            await session.close()
            raise CdpError(
                f"cannot reach Chrome remote debugging at {self._http_url} — enable "
                f"the chrome://inspect/#remote-debugging toggle ({exc})"
            ) from exc
        self._session = session
        self._ws = ws
        self._reader = asyncio.create_task(self._read_loop(ws))

    async def close(self) -> None:
        """Tear down the connection; safe to call in any state."""
        reader, self._reader = self._reader, None
        ws, self._ws = self._ws, None
        session, self._session = self._session, None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass
        self._fail_pending("CDP connection closed")

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CdpError(reason))
        self._pending.clear()

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                mid = data.get("id")
                fut = self._pending.get(mid) if mid is not None else None
                if fut is not None and not fut.done():
                    fut.set_result(data)
                # CDP events (no "id") are intentionally dropped — see module docstring.
        except Exception:  # noqa: BLE001 - a dying ws just ends the loop
            _log.debug("CDP reader ended", exc_info=True)
        finally:
            self._fail_pending("CDP connection closed")

    async def cmd(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one CDP command and await its response's ``result`` object.

        ``session_id`` scopes the command to an attached tab (flattened-session
        protocol); without it the command addresses the browser itself.
        """
        ws = self._ws
        if ws is None or ws.closed:
            raise CdpError("not connected")
        mid = next(self._ids)
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[mid] = fut
        try:
            await ws.send_str(json.dumps(msg))
            reply = await asyncio.wait_for(fut, timeout or self._timeout)
        except asyncio.TimeoutError as exc:
            raise CdpError(f"{method}: timed out") from exc
        finally:
            self._pending.pop(mid, None)
        if "error" in reply:
            raise CdpError(f"{method}: {reply['error'].get('message', 'CDP error')}")
        result = reply.get("result", {})
        return result if isinstance(result, dict) else {}

    async def open_tab(self, url: str) -> str:
        """Open ``url`` in a new tab of the operator's browser; returns the targetId."""
        result = await self.cmd("Target.createTarget", {"url": url})
        return str(result["targetId"])

    async def attach(self, target_id: str) -> str:
        """Attach to a tab (flattened session); returns the sessionId for ``cmd``."""
        result = await self.cmd(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        return str(result["sessionId"])

    async def evaluate(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = False,
        timeout: float | None = None,
    ) -> Any:
        """Run JS in the tab's main world and return its JSON-serialized value.

        Raises :class:`CdpError` when the expression itself throws — action helpers
        never should (they catch and return ``{"ok": false}`` payloads), so a raise
        here means the harness JS is broken, not the page.
        """
        result = await self.cmd(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            session_id=session_id,
            timeout=timeout,
        )
        details = result.get("exceptionDetails")
        if details:
            described = details.get("exception", {}).get("description") or details.get(
                "text", "evaluate failed"
            )
            raise CdpError(f"page JS raised: {str(described)[:400]}")
        inner = result.get("result", {})
        return inner.get("value") if isinstance(inner, dict) else None
