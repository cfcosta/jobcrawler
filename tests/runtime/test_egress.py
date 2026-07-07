"""Tests for the #19b OS-enforced egress allowlist — data plane + governance.

The data plane (the allowlisting loopback proxy) is pure-stdlib and portable, so
these run on any platform. A tiny local origin on 127.0.0.1 stands in for a board;
its host is passed as ``internal_exempt`` so the resolve-and-pin screen (which would
otherwise drop 127.0.0.1 as loopback) lets the test traffic through — that screen is
covered directly against :func:`screen_resolved_ip`. The seccomp firm-rung tests
(Stage 2) live behind their own platform skips; Stage 1 ships the portable layers and
the honest degrade ladder.
"""

from __future__ import annotations

import http.server
import socket
import threading
from collections.abc import Iterator

import httpx
import pytest

from jobcrawler import config
from jobcrawler.runtime import egress
from jobcrawler.runtime.egress import AllowlistProxy, EgressGuard
from jobcrawler.runtime.ssrf import screen_resolved_ip


class _Origin(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        body = b"OK:" + self.path.encode("latin-1")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:  # silence the test log
        pass


@pytest.fixture
def origin() -> Iterator[tuple[str, int]]:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[0], server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def proxy_to_origin(origin: tuple[str, int]) -> Iterator[AllowlistProxy]:
    host, _port = origin
    # ``host`` (127.0.0.1) is both on the allowlist and internal-exempt so the loopback
    # origin is reachable; a real board host is public and needs no exemption.
    proxy = AllowlistProxy([host], internal_exempt=[host])
    proxy.start()
    try:
        yield proxy
    finally:
        proxy.close()


# -- resolve-and-pin screen ----------------------------------------------------


def test_screen_resolved_ip_passes_a_public_address() -> None:
    assert screen_resolved_ip("93.184.216.34") is True  # example.com, a real public IP


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918 private
        "169.254.169.254",  # link-local cloud metadata
        "::1",  # v6 loopback
        "::ffff:169.254.169.254",  # v4-mapped metadata — must be unwrapped and blocked
        "::ffff:10.0.0.5",  # v4-mapped private
        "not-an-ip",  # unparseable → fail closed
    ],
)
def test_screen_resolved_ip_blocks_internal_and_mapped(ip: str) -> None:
    assert screen_resolved_ip(ip) is False


# -- data plane: the loopback proxy --------------------------------------------


def test_proxy_url_is_a_v4_literal_never_localhost(
    proxy_to_origin: AllowlistProxy,
) -> None:
    # 'localhost' could resolve ::1 first (a family the firm rung treats differently);
    # the child must be pointed at the literal loopback.
    assert proxy_to_origin.url.startswith("http://127.0.0.1:")


def test_forward_reaches_an_allowed_origin(
    proxy_to_origin: AllowlistProxy, origin: tuple[str, int]
) -> None:
    host, port = origin
    with httpx.Client(proxy=proxy_to_origin.url, timeout=5.0) as client:
        resp = client.get(f"http://{host}:{port}/hello")
    assert resp.status_code == 200
    assert resp.text == "OK:/hello"


def test_forward_refuses_an_off_domain_host(
    proxy_to_origin: AllowlistProxy, origin: tuple[str, int]
) -> None:
    # The origin's real port, but a host that is not on the allowlist → the proxy
    # answers 403 without ever dialing (the allowlist gate, relocated out of process).
    _host, port = origin
    with httpx.Client(proxy=proxy_to_origin.url, timeout=5.0) as client:
        resp = client.get(f"http://not-allowed.example:{port}/x")
    assert resp.status_code == 403


def test_connect_tunnel_gates_off_domain(proxy_to_origin: AllowlistProxy) -> None:
    # A raw CONNECT to an off-domain host is refused with 403 (the https path's gate),
    # before any tunnel is established.
    raw = socket.create_connection(("127.0.0.1", proxy_to_origin.port), timeout=5.0)
    try:
        raw.sendall(
            b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n\r\n"
        )
        reply = raw.recv(256)
    finally:
        raw.close()
    assert reply.startswith(b"HTTP/1.1 403")


def test_connect_tunnel_splices_an_allowed_target(
    proxy_to_origin: AllowlistProxy, origin: tuple[str, int]
) -> None:
    # An allowed CONNECT establishes the tunnel, then the proxy blind-splices bytes —
    # so plain HTTP spoken through the tunnel reaches the origin end to end.
    host, port = origin
    raw = socket.create_connection(("127.0.0.1", proxy_to_origin.port), timeout=5.0)
    try:
        raw.sendall(
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode()
        )
        established = raw.recv(128)
        assert established.startswith(b"HTTP/1.1 200")
        raw.sendall(
            f"GET /tunnel HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        body = b""
        while True:
            chunk = raw.recv(4096)
            if not chunk:
                break
            body += chunk
    finally:
        raw.close()
    assert b"200" in body and b"OK:/tunnel" in body


# -- governance: EgressGuard postures ------------------------------------------


def test_guard_off_bypasses_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_EGRESS_ENFORCE", "off")
    with EgressGuard(["example.com"], enforce=config.egress_enforce()) as guard:
        assert guard.refused is None
        assert guard.posture is not None and guard.posture.rung == "none"
        assert guard.env == {}  # no proxy override — the legacy direct client


def test_guard_auto_degrades_to_soft_and_wires_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stage 1 has no firm OS rung, so `auto` degrades to the soft (proxy-only) posture
    # and points every child fetch path at the loopback chokepoint.
    monkeypatch.delenv("JOBCRAWLER_EGRESS_ENFORCE", raising=False)
    with EgressGuard(["example.com"], enforce=config.egress_enforce()) as guard:
        assert guard.refused is None
        assert guard.posture is not None and guard.posture.rung == "soft"
        assert guard.posture.os_enforced is False
        env = guard.env or {}
        loopback = env["JOBCRAWLER_EGRESS_PROXY"]
        assert loopback.startswith("http://127.0.0.1:")
        # trust_env httpx clients (bare Ctx, solver) route via these; NO_PROXY cleared.
        assert env["HTTPS_PROXY"] == loopback and env["ALL_PROXY"] == loopback
        assert env["NO_PROXY"] == "" and env["https_proxy"] == loopback


def test_guard_require_refuses_when_no_os_rung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `require` fails closed: with no firm rung available it sets `refused` (the sandbox
    # raises EgressRefused and skips the site) rather than crawl uncontained.
    monkeypatch.setenv("JOBCRAWLER_EGRESS_ENFORCE", "require")
    with EgressGuard(["example.com"], enforce=config.egress_enforce()) as guard:
        assert guard.refused is not None
        assert guard.posture is not None and guard.posture.os_enforced is False


def test_arm_child_if_selected_is_a_noop_without_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No parent selection ⇒ the child's first-line arm hook does nothing (the proxy env
    # is already in force); it must never raise on the portable path.
    monkeypatch.delenv("JOBCRAWLER_EGRESS_ARM", raising=False)
    egress.arm_child_if_selected()


# -- config: the tri-state gate ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "auto"),
        ("auto", "auto"),
        ("require", "require"),
        ("off", "off"),
        ("on", "require"),
        ("1", "require"),
        ("true", "require"),
        ("yes", "require"),
        ("0", "off"),
        ("false", "off"),
        ("no", "off"),
        ("garbage", "auto"),  # unknown → the safe, non-crawl-breaking default
    ],
)
def test_egress_enforce_tristate(
    monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: str
) -> None:
    if raw is None:
        monkeypatch.delenv("JOBCRAWLER_EGRESS_ENFORCE", raising=False)
    else:
        monkeypatch.setenv("JOBCRAWLER_EGRESS_ENFORCE", raw)
    assert config.egress_enforce() == expected
