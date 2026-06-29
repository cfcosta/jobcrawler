"""Tests for operator notifications on autonomous actions (§4.8/§4.11).

``notify`` is the daemon's push channel for self-modifying events: it always logs and,
when a webhook is configured, *also* POSTs a JSON payload — best-effort and on a
background thread, so a slow or broken sink can never delay or abort the action it
reports. These tests pin the log-always behaviour, the webhook on/off branch, the
fire-and-forget delivery, the http(s)-only guard, and the swallow-all error contract.
"""

from __future__ import annotations

import logging
import threading

import pytest

from bizup_autocrawler import config, notify


def test_notify_always_logs_event_and_fields(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "notify_webhook", lambda: None)
    with caplog.at_level(logging.INFO, logger="bizup_autocrawler.notify"):
        notify.notify(
            "heal.promoted", "self-healed acme", slug="acme", outcome="promoted"
        )
    line = next(
        r.getMessage()
        for r in caplog.records
        if "event=heal.promoted" in r.getMessage()
    )
    assert "self-healed acme" in line
    assert "slug=acme" in line and "outcome=promoted" in line


def test_notify_without_a_webhook_does_not_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "notify_webhook", lambda: None)
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        notify, "_dispatch", lambda url, payload: calls.append((url, payload))
    )
    notify.notify("generator.adopted", "adopted", path="generators/g1.json")
    assert calls == []  # log-only when no webhook is configured


def test_notify_with_a_webhook_dispatches_the_full_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "notify_webhook", lambda: "https://hook.example/notify")
    captured: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        notify, "_dispatch", lambda url, payload: captured.append((url, payload))
    )

    notify.notify(
        "generator.adopted", "adopted g1", path="generators/g1.json", margin=12.5
    )

    assert len(captured) == 1
    url, payload = captured[0]
    assert url == "https://hook.example/notify"
    assert payload == {
        "event": "generator.adopted",
        "message": "adopted g1",
        "path": "generators/g1.json",
        "margin": 12.5,
    }


def test_dispatch_delivers_in_the_background_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _dispatch hands off to a daemon thread; the caller returns immediately and the
    # delivery still runs. A short join on an Event keeps the assertion deterministic.
    delivered = threading.Event()
    seen: list[tuple[str, dict[str, object]]] = []

    def fake_deliver(url: str, payload: dict[str, object]) -> None:
        seen.append((url, payload))
        delivered.set()

    monkeypatch.setattr(notify, "_deliver", fake_deliver)
    notify._dispatch("https://hook.example", {"event": "x"})
    assert delivered.wait(timeout=2.0)  # the background thread ran
    assert seen == [("https://hook.example", {"event": "x"})]


def test_deliver_posts_json_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import urllib.request

    captured: dict[str, object] = {}

    class _Resp:
        def close(self) -> None:
            pass

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0.0) -> _Resp:
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["ctype"] = req.headers.get("Content-type")
        captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(config, "notify_timeout", lambda: 7.0)

    notify._deliver("https://hook.example/x", {"event": "heal.failed", "slug": "acme"})

    assert captured["url"] == "https://hook.example/x"
    assert captured["method"] == "POST"
    assert captured["ctype"] == "application/json"
    assert captured["body"] == {"event": "heal.failed", "slug": "acme"}
    assert captured["timeout"] == 7.0


def test_deliver_refuses_a_non_http_scheme(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A misconfigured file:// URL must never be opened — that would turn an operator
    # alert into a local file read. The POST machinery is never reached.
    import urllib.request

    def boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("urlopen must not be called for a non-http scheme")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with caplog.at_level(logging.WARNING, logger="bizup_autocrawler.notify"):
        notify._deliver("file:///etc/passwd", {"event": "x"})
    assert any("only http(s)" in r.getMessage() for r in caplog.records)


def test_deliver_swallows_post_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A broken webhook must not propagate — the autonomous action already happened.
    import urllib.request

    def raise_oserror(*args: object, **kwargs: object) -> object:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raise_oserror)
    monkeypatch.setattr(config, "notify_timeout", lambda: 1.0)
    with caplog.at_level(logging.WARNING, logger="bizup_autocrawler.notify"):
        notify._deliver(
            "https://hook.example/x", {"event": "generator.adopted"}
        )  # must not raise
    assert any("POST failed" in r.getMessage() for r in caplog.records)


def test_notify_webhook_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_NOTIFY_WEBHOOK", raising=False)
    assert config.notify_webhook() is None


def test_notify_timeout_defaults_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCRAWLER_NOTIFY_TIMEOUT", raising=False)
    assert config.notify_timeout() == 10.0
    monkeypatch.setenv("AUTOCRAWLER_NOTIFY_TIMEOUT", "3.5")
    assert config.notify_timeout() == 3.5
