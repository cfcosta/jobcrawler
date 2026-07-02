"""Operator notifications for autonomous actions (§4.8/§4.11).

Whenever the daemon changes its own behaviour on its own initiative — self-heals a
crawler live, stages one for review, exhausts a self-heal, auto-adopts an optimized
generator, or rolls one back — it calls :func:`notify`. This is the operator's window
into the closed loop: by default each notification is one structured INFO log line, and
if ``AUTOCRAWLER_NOTIFY_WEBHOOK`` is set a small JSON payload is *also* POSTed there, so
an alert reaches a human (Slack/PagerDuty/an inbox) the moment the daemon rewrites its
own code, without anyone tailing logs.

Two hard rules:

  * **Best-effort, never blocking.** The log line is emitted synchronously; any webhook
    POST is handed to a daemon thread with a bounded timeout and every error swallowed.
    A slow or broken webhook must never delay or abort the autonomous action it reports
    (the action has already happened — the notification is downstream of it).
  * **Operator-facing only.** Payloads carry host-side facts (slug, outcome, generator
    path, margin) for a human; they are never read back by the generator/RLM, so this
    does not breach the §8 hidden-scorer invariant. Only ``http(s)`` URLs are honoured
    (a ``file://`` URL is refused) — the payload is JSON destined for an HTTP sink.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request

from bizup_autocrawler import config

_log = logging.getLogger("bizup_autocrawler.notify")


def notify(event: str, message: str, **fields: object) -> None:
    """Record an autonomous-action notification: always log, optionally POST a webhook.

    ``event`` is a short dotted category (e.g. ``heal.promoted``, ``generator.adopted``)
    and ``message`` is the human sentence; ``fields`` are extra structured key/values
    (slug, path, margin, …) folded into both the log line and the JSON payload. Returns
    immediately — any webhook delivery runs in the background and cannot raise.
    """
    _log.info("notify event=%s %s%s", event, message, _fmt_fields(fields))
    url = config.notify_webhook()
    if url:
        _dispatch(url, {"event": event, "message": message, **fields})


def _fmt_fields(fields: dict[str, object]) -> str:
    return "".join(f" {key}={value}" for key, value in fields.items())


def _dispatch(url: str, payload: dict[str, object]) -> None:
    """Deliver ``payload`` to ``url`` on a daemon thread (fire-and-forget).

    A seam in its own right: tests replace this to capture the (url, payload) without
    spawning a thread or touching the network.
    """
    threading.Thread(
        target=_deliver, args=(url, payload), name="notify-webhook", daemon=True
    ).start()


def _deliver(url: str, payload: dict[str, object]) -> None:
    """POST ``payload`` as JSON to ``url``; swallow every error (best-effort).

    Refuses any non-``http(s)`` scheme so a misconfigured ``file://`` URL can't turn an
    operator alert into a local file read; a delivery failure is logged at WARNING and
    dropped — the autonomous action it reports already completed.
    """
    if not url.lower().startswith(("http://", "https://")):
        _log.warning("notify webhook ignored: only http(s) is allowed, got %r", url)
        return
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=config.notify_timeout()).close()  # noqa: S310
    except Exception:
        _log.warning(
            "notify webhook POST failed (event=%s); continuing", payload.get("event")
        )
