"""Tests for the #19b OS-enforced egress allowlist — data plane + governance.

The data plane (the allowlisting loopback proxy) is pure-stdlib and portable, so
these run on any platform. A tiny local origin on 127.0.0.1 stands in for a board;
its host is passed as ``internal_exempt`` so the resolve-and-pin screen (which would
otherwise drop 127.0.0.1 as loopback) lets the test traffic through — that screen is
covered directly against :func:`screen_resolved_ip`. The governance tests force
:func:`egress_seccomp.supported` on/off so the degrade/refuse ladder is exercised
deterministically on any host; the seccomp firm-rung *mechanism* is proven end-to-end
by a test skipped off x86_64 Linux.
"""

from __future__ import annotations

import http.server
import os
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Iterator

import httpx
import pytest

from jobcrawler import config
from jobcrawler.runtime import egress, egress_seccomp
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
    # With no firm OS rung available, `auto` degrades to the soft (proxy-only) posture
    # and points every child fetch path at the loopback chokepoint.
    monkeypatch.delenv("JOBCRAWLER_EGRESS_ENFORCE", raising=False)
    monkeypatch.setattr(egress_seccomp, "supported", lambda: False)
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
    monkeypatch.setattr(egress_seccomp, "supported", lambda: False)
    with EgressGuard(["example.com"], enforce=config.egress_enforce()) as guard:
        assert guard.refused is not None
        assert guard.posture is not None and guard.posture.os_enforced is False


def test_guard_auto_selects_firm_rung_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Where the seccomp rung IS available, `auto` selects it: the posture is firm, the
    # child gets the JOBCRAWLER_EGRESS_ARM handshake env, and two pipe fds are passed.
    # (No child spawns here; __exit__ reclaims the pipe fds since attach() never runs.)
    monkeypatch.delenv("JOBCRAWLER_EGRESS_ENFORCE", raising=False)
    monkeypatch.setattr(egress_seccomp, "supported", lambda: True)
    with EgressGuard(["example.com"], enforce=config.egress_enforce()) as guard:
        assert guard.refused is None
        assert guard.posture is not None and guard.posture.rung == "firm"
        assert guard.posture.os_enforced is True
        env = guard.env or {}
        arm = env["JOBCRAWLER_EGRESS_ARM"]
        hs_w, ack_r, port, mode = arm.split(",")
        assert int(port) > 0 and mode == "auto"
        # the two handshake fds the child arms from are passed through untouched
        assert guard.pass_fds == (int(hs_w), int(ack_r))
        # the proxy env still funnels cooperative clients as a second layer
        assert env["JOBCRAWLER_EGRESS_PROXY"].startswith("http://127.0.0.1:")


def test_build_connect_filter_is_wellformed() -> None:
    # The classic-BPF assembler resolves every label to an in-range forward offset and
    # emits 8-byte sock_filter instructions — a bad jump would raise in _assemble().
    prog = egress_seccomp._build_connect_filter()
    assert len(prog) % 8 == 0 and len(prog) >= 8 * 20


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # AF_INET 127.0.0.1:4443 (family little-endian, port network order)
        (struct.pack("<H", socket.AF_INET) + struct.pack("!H", 4443) + bytes([127, 0, 0, 1]), ("127.0.0.1", 4443)),
        (struct.pack("<H", socket.AF_INET6) + b"\x00" * 26, None),  # v6 → not the proxy
        (b"\x02", None),  # too short
    ],
)
def test_parse_sockaddr(raw: bytes, expected: tuple[str, int] | None) -> None:
    assert egress_seccomp._parse_sockaddr(raw) == expected


_FIRM_CHILD = """
import errno, os, socket, struct, sys
sys.path.insert(0, {src!r})
from jobcrawler.runtime import egress_seccomp
hs_w, ack_r, host, port = {hs_w}, {ack_r}, {host!r}, {port}
fd = egress_seccomp.install_connect_listener()
os.write(hs_w, b"A" + struct.pack("i", fd)); os.close(hs_w)
if os.read(ack_r, 1) != b"G":
    os._exit(3)
os.close(ack_r); os.close(fd)
# a connect to the pinned proxy addr is brokered to the origin
s = socket.socket()
try:
    s.connect((host, port))
    s.sendall(b"hi"); reply = s.recv(64)
except OSError:
    os._exit(4)
allowed_ok = reply == b"ECHO:hi"
# a connect anywhere else is refused EACCES (the raw-escape case)
denied_ok = False
try:
    socket.socket().connect(("10.255.255.1", 9))
except OSError as exc:
    denied_ok = exc.errno == errno.EACCES
os._exit(0 if (allowed_ok and denied_ok) else 5)
"""


@pytest.mark.skipif(
    not egress_seccomp.supported(), reason="seccomp connect-broker is x86_64-Linux only"
)
def test_firm_rung_brokers_proxy_connect_and_denies_the_rest() -> None:
    # End-to-end proof of the shipped mechanism: a real child installs the filter, its
    # connect() to the pinned proxy addr is transparently brokered to a local origin
    # (ECHO round-trips), and its connect() to any other host is refused EACCES.
    echo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    echo.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    echo.bind(("127.0.0.1", 0))
    echo.listen(4)
    origin = echo.getsockname()

    def _serve() -> None:
        while True:
            try:
                conn, _ = echo.accept()
            except OSError:
                return
            data = conn.recv(64)
            conn.sendall(b"ECHO:" + data)
            conn.close()

    threading.Thread(target=_serve, daemon=True).start()

    # the import root the child adds to sys.path: the dir containing the jobcrawler pkg
    src = os.path.dirname(
        os.path.dirname(os.path.dirname(egress_seccomp.__file__))
    )
    hs_r, hs_w = os.pipe()
    ack_r, ack_w = os.pipe()
    os.set_inheritable(hs_w, True)
    os.set_inheritable(ack_r, True)
    script = _FIRM_CHILD.format(
        src=src, hs_w=hs_w, ack_r=ack_r, host=origin[0], port=origin[1]
    )
    broker = None
    proc = subprocess.Popen(
        [sys.executable, "-c", script], pass_fds=(hs_w, ack_r)
    )
    try:
        os.close(hs_w)
        os.close(ack_r)
        hdr = os.read(hs_r, 5)
        assert len(hdr) == 5 and hdr[:1] == b"A", f"child did not arm: {hdr!r}"
        listener_fd = struct.unpack("i", hdr[1:5])[0]
        broker = egress_seccomp.attach_broker(proc.pid, listener_fd, origin)
        assert broker is not None
        os.write(ack_w, b"G")
        rc = proc.wait(timeout=20)
        assert rc == 0, f"child exit {rc} (0=brokered+denied ok)"
    finally:
        for fd in (hs_r, ack_w):
            try:
                os.close(fd)
            except OSError:
                pass
        if broker is not None:
            broker.close()
        if proc.poll() is None:
            proc.kill()
        echo.close()


_FIRM_CHILD_MULTI = """
import os, socket, struct, sys
sys.path.insert(0, {src!r})
from jobcrawler.runtime import egress_seccomp
hs_w, ack_r, host, port, n = {hs_w}, {ack_r}, {host!r}, {port}, {n}
fd = egress_seccomp.install_connect_listener()
os.write(hs_w, b"A" + struct.pack("i", fd)); os.close(hs_w)
if os.read(ack_r, 1) != b"G":
    os._exit(3)
os.close(ack_r); os.close(fd)
ok = 0
for _ in range(n):
    s = socket.socket()
    try:
        s.connect((host, port)); s.sendall(b"hi")
        if s.recv(64) == b"ECHO:hi":
            ok += 1
    finally:
        s.close()
os._exit(0 if ok == n else 5)
"""


@pytest.mark.skipif(
    not egress_seccomp.supported(), reason="seccomp connect-broker is x86_64-Linux only"
)
def test_broker_does_not_leak_a_fd_per_brokered_connect() -> None:
    # Regression: SECCOMP_IOCTL_NOTIF_ADDFD *duplicates* the broker's socket into the child
    # (dup semantics — it refcounts the shared open file description), so the broker must
    # close its OWN copy after injecting it. When it detach()ed instead (returns the fd
    # without closing it), every brokered connect() leaked a socket in the long-lived
    # daemon — they piled up in CLOSE_WAIT until the fd table was exhausted and the §6
    # best-of-N fan-out died with OSError [Errno 24] Too many open files. So a child that
    # connects N times must NOT grow the parent's fd table by ~N.
    n = 16
    echo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    echo.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    echo.bind(("127.0.0.1", 0))
    echo.listen(8)
    origin = echo.getsockname()

    def _serve() -> None:
        while True:
            try:
                conn, _ = echo.accept()
            except OSError:
                return
            try:
                conn.recv(64)
                conn.sendall(b"ECHO:hi")
            finally:
                conn.close()

    threading.Thread(target=_serve, daemon=True).start()

    src = os.path.dirname(
        os.path.dirname(os.path.dirname(egress_seccomp.__file__))
    )
    hs_r, hs_w = os.pipe()
    ack_r, ack_w = os.pipe()
    os.set_inheritable(hs_w, True)
    os.set_inheritable(ack_r, True)
    script = _FIRM_CHILD_MULTI.format(
        src=src, hs_w=hs_w, ack_r=ack_r, host=origin[0], port=origin[1], n=n
    )
    broker = None
    proc = subprocess.Popen([sys.executable, "-c", script], pass_fds=(hs_w, ack_r))
    try:
        os.close(hs_w)
        os.close(ack_r)
        hdr = os.read(hs_r, 5)
        assert len(hdr) == 5 and hdr[:1] == b"A", f"child did not arm: {hdr!r}"
        listener_fd = struct.unpack("i", hdr[1:5])[0]
        broker = egress_seccomp.attach_broker(proc.pid, listener_fd, origin)
        assert broker is not None
        before = len(os.listdir("/proc/self/fd"))  # broker attached, before any connect
        os.write(ack_w, b"G")
        rc = proc.wait(timeout=20)
        assert rc == 0, f"child exit {rc} (0=all {n} connects brokered)"
        after = len(os.listdir("/proc/self/fd"))
        # The fix keeps this flat; the detach() leak grew it by ~n (one CLOSE_WAIT socket
        # per brokered connect). Generous slack for unrelated churn, still far below n.
        assert after - before <= 3, (
            f"broker leaked ~{after - before} fds over {n} connects (detach() regression)"
        )
    finally:
        for fd in (hs_r, ack_w):
            try:
                os.close(fd)
            except OSError:
                pass
        if broker is not None:
            broker.close()
        if proc.poll() is None:
            proc.kill()
        echo.close()


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
