"""OS-enforced egress allowlist for the sandbox child (#19b, §8).

The env scrub (#19a) removes the *loot* a read primitive could exfiltrate; this
module removes the *channel*. Two composed planes, gated by a detect-and-degrade
ladder and governed by ``config.egress_enforce()`` (``auto`` | ``require`` | ``off``):

  * DATA PLANE (always on when enforcement is not ``off``): a per-crawl allowlisting
    loopback proxy the *parent* binds on ``127.0.0.1:0``, seeded with this crawl's
    ``allowed_hosts``. It speaks HTTP ``CONNECT`` (https, kept end-to-end so the child
    still validates the origin certificate) and absolute-URI (http). Per request it
    (a) checks the hostname with :func:`urls.host_on_domain`; (b) resolves it *in the
    trusted parent*; (c) drops every candidate that fails :func:`ssrf.screen_resolved_ip`
    — closing the DNS-rebinding window ``check_url`` (name-only) leaves open; (d) pins
    and dials one surviving public IP with multi-IP fallback, then blind-splices bytes.
    The child does **zero** DNS. It chains through the operator's ``JOBCRAWLER_EGRESS_PROXY``
    as upstream when set. It is wired into every child fetch path via the child's proxy
    env vars (``HTTP(S)_PROXY``/``ALL_PROXY``/``JOBCRAWLER_EGRESS_PROXY`` → the loopback,
    empty ``NO_PROXY``) plus an explicit ``Ctx(proxy=…)`` param.

  * ENFORCEMENT PLANE (the OS layer, when the box supports it): a mechanism whose only
    job is "the child can emit packets to nothing but that loopback proxy", so even a
    raw socket a sandbox escape opens cannot reach a non-allowlisted host. Its rungs
    (seccomp connect-broker / netns) are probed and armed by :func:`_select_firm_rung`;
    where none is available the ladder degrades to the DATA PLANE alone and logs the
    honest posture (``soft``), and ``require`` mode refuses the crawl rather than run it
    uncontained.

The proxy alone is a *cooperative* boundary (like the in-process SSRF guard, just
relocated to another process) — it is labelled ``soft``, never "OS-enforced", until a
real OS rung is armed.
"""

from __future__ import annotations

import logging
import os
import select
import socket
import struct
import sys
import threading
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    import subprocess

    from jobcrawler.runtime.egress_seccomp import Broker

from jobcrawler import config
from jobcrawler.runtime.ssrf import screen_resolved_ip
from jobcrawler.urls import host_on_domain, normalize_host

_log = logging.getLogger("jobcrawler.egress")

# One resolved dial target: (address family, sockaddr). The sockaddr is a v4 2-tuple
# or v6 4-tuple as ``getaddrinfo`` returns it — kept opaque (``Any``) so ``connect``
# accepts either without the proxy caring about the shape.
_Candidate = tuple[int, tuple[Any, ...]]

# Bounded reads + per-proxy tunnel caps: the loopback proxy runs inside the trusted
# daemon, so an untrusted child must not be able to wedge it (slowloris headers, tunnel
# floods) or turn it into a memory sink.
_HEADER_CAP = 32 * 1024  # max request-line + headers before we refuse (slowloris bound)
_MAX_TUNNELS = 64  # concurrent tunnels one child may hold open
_SPLICE_CHUNK = 65536
_IDLE_TIMEOUT = 30.0  # per-tunnel idle before teardown
_HARD_TIMEOUT = 600.0  # per-tunnel hard lifetime cap (matches collect wall clock)

# The child ``os._exit`` code when it armed the firm rung but cannot run contained
# (broker never attached, or a require-mode arm failure). The parent maps a non-zero
# child exit to a SandboxError, so the site is skipped (fail closed).
_EGRESS_CHILD_REFUSED = 97
# Budget for the parent to hear the child's "armed" handshake before degrading (arming
# is a couple of syscalls; a stall means the child wedged on import).
_ARM_HANDSHAKE_TIMEOUT = 10.0


class EgressRefused(RuntimeError):
    """``require`` mode: no real OS egress rung is available — the crawl is refused.

    Raised in the trusted parent *before* the child spawns; the sandbox maps it to a
    ``SandboxError``-shaped result so the daemon skips the site (fails closed) instead
    of running the untrusted crawler uncontained."""


class _Refused(Exception):
    """A single proxied request failed the allowlist / resolve-and-pin screen."""


# -- data plane: the allowlisting loopback proxy -------------------------------


class AllowlistProxy:
    """A per-crawl HTTP proxy on ``127.0.0.1:0`` that only reaches ``allowed_hosts``.

    Bound and served entirely in the trusted parent. ``upstream`` (the operator's
    ``JOBCRAWLER_EGRESS_PROXY``) is chained through when set — in which case the final
    dialed IP is the upstream's responsibility, so IP-pinning degrades to hostname-only.
    ``internal_exempt`` names hosts (e.g. a FlareSolverr endpoint) allowed to resolve to
    an otherwise-blocked internal IP so an enabled solver still reaches its endpoint
    through the one chokepoint.
    """

    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        upstream: str | None = None,
        internal_exempt: Collection[str] = (),
    ) -> None:
        self._allowed = {h.lower() for h in allowed_hosts}
        self._upstream = upstream
        self._exempt = {h.lower() for h in internal_exempt}
        self._resolve_timeout = config.egress_resolve_timeout()
        self._sem = threading.BoundedSemaphore(_MAX_TUNNELS)
        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(128)
        self.port: int = self._srv.getsockname()[1]
        self._thread = threading.Thread(
            target=self._serve, name="egress-proxy", daemon=True
        )

    @property
    def url(self) -> str:
        # A v4 literal, never "localhost": a name could resolve ::1 (AF_INET6) first,
        # a family the firm rung's broker/filter treats differently.
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        self._srv.settimeout(0.5)  # poll the stop flag rather than block accept() forever
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._sem.acquire(blocking=False):
                conn.close()  # over the per-child tunnel cap — refuse by hangup
                continue
            threading.Thread(
                target=self._handle_wrap, args=(conn,), daemon=True
            ).start()

    def _handle_wrap(self, conn: socket.socket) -> None:
        try:
            self._handle(conn)
        except _Refused as exc:
            _refuse(conn, str(exc))
        except Exception:  # never let a proxy-thread crash take down the daemon
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self._sem.release()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(_IDLE_TIMEOUT)
        head, leftover = _read_headers(conn)
        line, _, header_block = head.partition(b"\r\n")
        parts = line.split(b" ")
        if len(parts) < 3:
            raise _Refused("malformed request line")
        method, target = parts[0].upper(), parts[1]
        if method == b"CONNECT":
            self._do_connect(conn, target, leftover)
        else:
            self._do_forward(conn, method, target, header_block, leftover)

    def _do_connect(
        self, conn: socket.socket, target: bytes, leftover: bytes
    ) -> None:
        host, port = _split_hostport(target, 443)
        if self._upstream is not None:
            self._gate_host(host)
            up = self._dial_upstream()
            # Forward the client's CONNECT verbatim; the upstream returns its own
            # 200 through the splice. IP-pinning is the upstream's job here.
            up.sendall(b"CONNECT " + target + b" HTTP/1.1\r\nHost: " + target + b"\r\n\r\n")
            _splice(conn, up)
            return
        up = self._dial_direct(host, port)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        if leftover:
            up.sendall(leftover)
        _splice(conn, up)

    def _do_forward(
        self,
        conn: socket.socket,
        method: bytes,
        target: bytes,
        header_block: bytes,
        leftover: bytes,
    ) -> None:
        # Absolute-URI http forward proxy: httpx sends `GET http://host/path` for plain
        # http (and http robots.txt / https→http redirect hops) rather than a CONNECT.
        parts = urlsplit(target.decode("latin-1"))
        if parts.scheme.lower() != "http" or not parts.hostname:
            raise _Refused("only absolute-URI http is forwarded")
        host = parts.hostname.lower()
        port = parts.port or 80
        if self._upstream is not None:
            self._gate_host(host)
            up = self._dial_upstream()
            up.sendall(
                method + b" " + target + b" HTTP/1.1\r\n" + header_block + b"\r\n\r\n" + leftover
            )
            _splice(conn, up)
            return
        up = self._dial_direct(host, port)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        request = (
            method + b" " + path.encode("latin-1") + b" HTTP/1.1\r\n"
            + _rewrite_headers(header_block) + b"\r\n" + leftover
        )
        up.sendall(request)
        _splice(conn, up)

    def _gate_host(self, host: str) -> None:
        if host not in self._exempt and not host_on_domain(host, self._allowed):
            raise _Refused(f"off-domain host: {host}")

    def _dial_upstream(self) -> socket.socket:
        assert self._upstream is not None
        raw = self._upstream if "://" in self._upstream else "http://" + self._upstream
        up = urlsplit(raw)
        host, port = up.hostname, up.port or 80
        if not host:
            raise _Refused("malformed upstream proxy url")
        return socket.create_connection((host, port), timeout=self._resolve_timeout)

    def _dial_direct(self, host: str, port: int) -> socket.socket:
        """Resolve-and-pin: dial one screened public IP, falling back through the rest."""
        candidates = self._resolve_and_pin(host, port)
        last: OSError | None = None
        for family, sockaddr in candidates:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(self._resolve_timeout)
                sock.connect(sockaddr)
                sock.settimeout(_IDLE_TIMEOUT)
                return sock
            except OSError as exc:
                last = exc
                try:
                    sock.close()
                except OSError:
                    pass
        raise _Refused(f"all candidates failed for {host}: {last}")

    def _resolve_and_pin(self, host: str, port: int) -> list[_Candidate]:
        self._gate_host(host)
        exempt = host in self._exempt
        try:
            infos = self._getaddrinfo(host, port)
        except OSError as exc:
            raise _Refused(f"resolve failed for {host}: {exc}")
        out: list[_Candidate] = []
        seen: set[_Candidate] = set()
        for family, sockaddr in infos:
            ip = str(sockaddr[0])
            if not exempt and not screen_resolved_ip(ip):
                continue  # a name that (re)resolves to an internal IP is dropped here
            key = (family, sockaddr)
            if key not in seen:
                seen.add(key)
                out.append((family, sockaddr))
        if not out:
            raise _Refused(f"no public IP for {host}")
        return out

    def _getaddrinfo(self, host: str, port: int) -> list[_Candidate]:
        # getaddrinfo has no native timeout; run it in a throwaway daemon thread so a
        # slow resolver can't wedge a child's fetch past egress_resolve_timeout. Reduce
        # each 5-tuple to the (family, sockaddr) pair the dialer needs.
        result: list[_Candidate] = []
        error: list[BaseException] = []

        def _run() -> None:
            try:
                for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                    result.append((int(info[0]), tuple(info[4])))
            except Exception as exc:  # gaierror is an OSError subclass
                error.append(exc)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(self._resolve_timeout)
        if worker.is_alive():
            raise OSError(f"resolve timed out after {self._resolve_timeout}s")
        if error:
            raise error[0]
        return result


def _read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    """Read up to the header terminator with a hard byte cap; return (head, leftover)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        if len(buf) > _HEADER_CAP:
            raise _Refused("headers exceed cap")
        try:
            chunk = sock.recv(4096)
        except OSError as exc:
            raise _Refused(f"read error: {exc}")
        if not chunk:
            raise _Refused("eof before end of headers")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _rewrite_headers(header_block: bytes) -> bytes:
    """Origin-form header block: drop hop-by-hop proxy headers, force ``Connection: close``.

    Closing the origin connection per request keeps the forward path a simple one-shot
    relay (no Content-Length/chunked bookkeeping); httpx transparently reconnects."""
    kept: list[bytes] = []
    for raw in header_block.split(b"\r\n"):
        if not raw:
            continue
        name = raw.split(b":", 1)[0].strip().lower()
        if name in (b"connection", b"proxy-connection"):
            continue
        kept.append(raw)
    kept.append(b"Connection: close")
    return b"\r\n".join(kept) + b"\r\n"


def _split_hostport(raw: bytes, default: int) -> tuple[str, int]:
    text = raw.decode("latin-1")
    if text.startswith("["):  # [ipv6]:port
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = text.partition(":")
    return host.lower(), int(port) if port else default


def _refuse(conn: socket.socket, reason: str) -> None:
    _log.debug("egress proxy refused: %s", reason)
    try:
        conn.sendall(
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )
    except OSError:
        pass


def _splice(a: socket.socket, b: socket.socket) -> None:
    """Blind bidirectional byte relay until either side closes / idles / times out."""
    deadline = time.monotonic() + _HARD_TIMEOUT
    socks = [a, b]
    try:
        while True:
            budget = min(_IDLE_TIMEOUT, deadline - time.monotonic())
            if budget <= 0:
                return
            readable, _, errored = select.select(socks, [], socks, budget)
            if errored or not readable:  # error or idle timeout
                return
            for src in readable:
                dst = b if src is a else a
                try:
                    data = src.recv(_SPLICE_CHUNK)
                except OSError:
                    return
                if not data:
                    return  # EOF one side → tear the tunnel down
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        for sock in (a, b):
            try:
                sock.close()
            except OSError:
                pass


# -- enforcement ladder + guard ------------------------------------------------


@dataclass(frozen=True)
class Posture:
    """The egress posture chosen for one crawl — logged honestly every time."""

    rung: str  # "hard" | "firm" | "soft" | "none"
    detail: str
    os_enforced: bool  # True only for "hard"/"firm"


@dataclass
class _FirmRung:
    """A selected OS-enforcement rung: env the child arms from, fds to pass, its posture.

    The remaining fields are the parent-side handshake state :meth:`EgressGuard.attach`
    uses after the child spawns: ``hs_read_fd`` (the parent reads the child's "armed" +
    listener-fd message), ``ack_write_fd`` (the parent releases or aborts the child),
    ``child_pass_fds`` (the child's ends the parent must close post-spawn so reads EOF),
    and ``proxy_addr`` (the one destination the broker will broker a connect to)."""

    env: dict[str, str] = field(default_factory=dict)
    pass_fds: tuple[int, ...] = ()
    posture: Posture = field(
        default_factory=lambda: Posture("firm", "os-enforced", True)
    )
    hs_read_fd: int = -1
    ack_write_fd: int = -1
    child_pass_fds: tuple[int, ...] = ()
    proxy_addr: tuple[str, int] = ("127.0.0.1", 0)


def _select_firm_rung(enforce: str, proxy: AllowlistProxy) -> _FirmRung | None:
    """Probe + select the strongest OS-enforcement rung, or ``None`` if none is armable.

    On x86_64 Linux this selects the seccomp ``SECCOMP_RET_USER_NOTIF`` connect-broker
    (:mod:`jobcrawler.runtime.egress_seccomp`): it opens the two handshake pipes and
    builds the ``JOBCRAWLER_EGRESS_ARM`` env the child arms from. Selection here only
    *sets up* the rung; :meth:`EgressGuard.attach` confirms the child actually armed
    (post-spawn) before the posture is trusted as ``firm``. Returning ``None`` (no
    supported OS rung) degrades ``auto`` to the ``soft`` proxy-only posture and makes
    ``require`` refuse — both correct, fail-safe outcomes."""
    from jobcrawler.runtime import egress_seccomp

    if not egress_seccomp.supported():
        return None
    # child->parent: the "armed" byte + listener fd number; parent->child: go/abort ack.
    hs_read, hs_write = os.pipe()
    ack_read, ack_write = os.pipe()
    os.set_inheritable(hs_write, True)
    os.set_inheritable(ack_read, True)
    env = {"JOBCRAWLER_EGRESS_ARM": f"{hs_write},{ack_read},{proxy.port},{enforce}"}
    return _FirmRung(
        env=env,
        pass_fds=(hs_write, ack_read),
        posture=Posture(
            "firm", "seccomp connect-broker (SECCOMP_RET_USER_NOTIF)", True
        ),
        hs_read_fd=hs_read,
        ack_write_fd=ack_write,
        child_pass_fds=(hs_write, ack_read),
        proxy_addr=("127.0.0.1", proxy.port),
    )


def _solver_exempt_hosts() -> tuple[str, ...]:
    """The FlareSolverr endpoint host (if a solver is configured) — proxy-exempt.

    An enabled solver's client must still reach its (often internal) endpoint through the
    one chokepoint, so its host skips the resolved-IP screen. Default ``solver=none`` →
    empty, so nothing is exempted."""
    url = os.getenv("FLARESOLVERR_URL")
    if not url:
        return ()
    host = normalize_host(url) or (urlsplit(url).hostname or "")
    return (host.lower(),) if host else ()


class EgressGuard:
    """Parent-side context manager wrapping a sandbox-child spawn (#19b).

    On ``__enter__`` it starts the data-plane proxy, selects the strongest OS rung, and
    exposes ``env`` (child env overrides), ``pass_fds``, and ``posture``. In ``require``
    mode with no real OS rung it sets ``refused`` (the sandbox raises
    :class:`EgressRefused` and skips the site).

    When a firm rung was selected the caller must, right after spawning the child and
    before it crawls, call :meth:`attach` with the ``Popen`` handle: the child blocks
    having armed its seccomp filter, and :meth:`attach` copies its listener fd, starts
    the broker, and releases it. ``__exit__`` stops the broker and tears the proxy down.
    """

    def __init__(self, allowed_hosts: Collection[str], *, enforce: str) -> None:
        self._allowed = list(allowed_hosts)
        self._enforce = enforce
        self._proxy: AllowlistProxy | None = None
        self._firm: _FirmRung | None = None
        self._broker: Broker | None = None
        self._attached = False
        self.env: dict[str, str] | None = None
        self.pass_fds: tuple[int, ...] = ()
        self.posture: Posture | None = None
        self.refused: str | None = None

    def __enter__(self) -> EgressGuard:
        if self._enforce == "off":
            self.posture = Posture("none", "enforcement off (JOBCRAWLER_EGRESS_ENFORCE=off)", False)
            self.env = {}
            _log.info("os_egress: off — legacy direct client (mechanism bypassed)")
            return self
        try:
            self._proxy = AllowlistProxy(
                self._allowed,
                upstream=config.egress_proxy(),  # operator upstream, captured pre-override
                internal_exempt=_solver_exempt_hosts(),
            )
            self._proxy.start()
        except OSError as exc:
            # Rung D: even the loopback proxy failed to bind.
            self._proxy = None
            if self._enforce == "require":
                self.refused = f"egress proxy failed to bind: {exc}"
                self.posture = Posture("none", self.refused, False)
                _log.critical("os_egress: REQUIRE but proxy bind failed — refusing crawl")
                return self
            self.posture = Posture("none", f"proxy bind failed ({exc}); legacy direct client", False)
            self.env = {}
            _log.critical("os_egress: proxy bind failed (%s) — legacy direct client", exc)
            return self

        loopback = self._proxy.url
        proxy_env = _proxy_env(loopback)
        firm = _select_firm_rung(self._enforce, self._proxy)
        if firm is None:
            if self._enforce == "require":
                self.refused = "no OS egress enforcement rung available"
                self.posture = Posture("none", self.refused, False)
                self._proxy.close()
                self._proxy = None
                _log.critical("os_egress: REQUIRE but no OS rung available — refusing crawl")
                return self
            self.posture = Posture(
                "soft", "in-python loopback proxy only; a raw-socket escape is NOT OS-contained", False
            )
            self.env = proxy_env
            _log.warning(
                "os_egress: soft (in-python loopback proxy only) — a raw-socket escape is "
                "NOT OS-contained; relying on #19a/#20/#21"
            )
            return self

        self._firm = firm
        self.posture = firm.posture
        self.env = {**proxy_env, **firm.env}
        self.pass_fds = firm.pass_fds
        _log.info("os_egress: %s (%s) — pending child arm", firm.posture.rung, firm.posture.detail)
        return self

    def attach(self, proc: subprocess.Popen[Any]) -> None:
        """Post-spawn: complete the firm-rung handshake and start the broker (#19b).

        No-op unless a firm rung was selected. Closes the parent's copies of the child's
        pipe ends (so a subsequent read sees EOF when the child exits), waits for the
        child's "armed" message, copies the listener fd via ``pidfd_getfd``, starts the
        broker, and releases the child with ``b"G"``. If the child could not arm, or the
        broker cannot attach, the child's ack pipe is closed *without* ``b"G"`` — the
        child then fails closed (``require``) or the posture is downgraded to ``soft``
        (``auto``). Never raises: any error degrades rather than crashing the spawn."""
        firm = self._firm
        if firm is None:
            return
        self._attached = True
        for fd in firm.child_pass_fds:  # release the child's ends we still hold
            _close(fd)
        try:
            hs = _read_handshake(firm.hs_read_fd, _ARM_HANDSHAKE_TIMEOUT)
            if len(hs) == 5 and hs[:1] == b"A":
                from jobcrawler.runtime import egress_seccomp

                child_listener_fd = struct.unpack("i", hs[1:5])[0]
                broker = egress_seccomp.attach_broker(
                    proc.pid, child_listener_fd, firm.proxy_addr
                )
                if broker is not None:
                    self._broker = broker
                    os.write(firm.ack_write_fd, b"G")  # release the child to crawl
                    _log.info("os_egress: firm rung armed (seccomp connect-broker attached)")
                    return
                _log.error("os_egress: child armed but broker attach failed — aborting child")
            elif hs[:1] == b"F":
                # The child reported it could not arm. In `require` it has already
                # `os._exit`ed (the non-zero code → SandboxError); in `auto` it kept
                # running on the proxy env, so reflect the honest soft posture.
                if self._enforce == "require":
                    _log.critical("os_egress: REQUIRE but child could not arm seccomp — crawl refused")
                else:
                    self._degrade("child could not arm seccomp")
            else:  # empty (timeout / child died) or malformed
                self._degrade("no armed handshake from child")
        finally:
            _close(firm.ack_write_fd)  # a closed ack w/o "G" makes the child fail closed
            _close(firm.hs_read_fd)

    def _degrade(self, why: str) -> None:
        """Downgrade a would-be firm crawl to the soft (proxy-only) posture, honestly logged."""
        self.posture = Posture("soft", f"{why}; data-plane proxy only", False)
        _log.warning("os_egress: firm rung unavailable at runtime (%s) — posture soft", why)

    def __exit__(self, *exc: object) -> bool:
        if self._broker is not None:
            self._broker.close()
            self._broker = None
        if self._firm is not None and not self._attached:
            # attach() never ran (spawn failed before it) — reclaim its pipe fds.
            for fd in (self._firm.hs_read_fd, self._firm.ack_write_fd, *self._firm.child_pass_fds):
                _close(fd)
        if self._proxy is not None:
            self._proxy.close()
            self._proxy = None
        return False


def arm_child_if_selected() -> None:
    """Child-side: arm the OS-enforcement rung the parent selected, if any (#19b).

    Called as the very first line of each sandbox entry's ``main()``. When the parent
    armed a firm rung it exports ``JOBCRAWLER_EGRESS_ARM`` (``hs_w,ack_r,proxy_port,mode``);
    the child installs the seccomp filter here — in its OWN ``main``, since a user-notify
    filter must be installed by the task it governs, never in a ``preexec_fn`` — then:

      1. writes ``b"A"`` + its listener fd number to the handshake pipe;
      2. blocks reading the parent's one-byte ack (the parent copies the listener via
         ``pidfd_getfd`` and starts the broker before releasing us);
      3. on ``b"G"`` closes its own listener copy (so escaped crawler code cannot
         ``NOTIF_RECV``/``SEND`` to self-approve its traps) and returns to run the crawl.

    A failure to arm, or an aborted handshake, either degrades to the data-plane proxy
    (``auto``) or refuses the crawl via ``os._exit`` (``require``). When the variable is
    unset (no firm rung, offline replay) this is a no-op — the data-plane proxy wired via
    the child's proxy env vars is already in force regardless."""
    raw = os.getenv("JOBCRAWLER_EGRESS_ARM")
    if not raw:
        return
    try:
        hs_w_s, ack_r_s, _port_s, mode = raw.split(",", 3)
        hs_w, ack_r = int(hs_w_s), int(ack_r_s)
    except (ValueError, AttributeError):
        return  # malformed arm var; the proxy env still routes cooperative clients

    from jobcrawler.runtime import egress_seccomp

    try:
        listener = egress_seccomp.install_connect_listener()
    except OSError as exc:
        _child_arm_failed(hs_w, mode, exc)
        return
    try:
        os.write(hs_w, b"A" + struct.pack("i", listener))
        _close(hs_w)
        ack = _read_exact(ack_r, 1)
        _close(ack_r)
    except OSError as exc:
        # The filter is already installed; without the broker every connect() would trap
        # unanswered. Refuse loudly rather than run wedged.
        _refuse_and_exit(f"egress handshake failed: {exc}")
        return
    if ack != b"G":
        _refuse_and_exit("egress broker did not attach")
        return
    # The parent has copied the listener via pidfd_getfd; drop our handle before any
    # untrusted crawler code runs so it cannot answer its own connect() traps.
    _close(listener)


def _child_arm_failed(hs_w: int, mode: str, exc: OSError) -> None:
    """Report an arm failure to the parent, then refuse (``require``) or degrade (``auto``)."""
    try:
        os.write(hs_w, b"F")
        _close(hs_w)
    except OSError:
        pass
    if mode == "require":
        _refuse_and_exit(f"failed to arm OS egress enforcement: {exc}")
        return
    _log.warning(
        "os_egress: could not arm seccomp (%s); data-plane proxy still routes", exc
    )


def _refuse_and_exit(msg: str) -> None:
    """Fail closed from the child: the crawl must not run uncontained (``require``)."""
    sys.stderr.write(f"jobcrawler egress: {msg}; refusing to run uncontained\n")
    sys.stderr.flush()
    os._exit(_EGRESS_CHILD_REFUSED)


def _read_exact(fd: int, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``fd`` (short read → the writer closed early)."""
    buf = b""
    while len(buf) < n:
        chunk = os.read(fd, n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _close(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _read_handshake(fd: int, timeout: float) -> bytes:
    """Parent-side: read the child's armed handshake (``b"A"``+fd, or ``b"F"``) with a cap.

    ``select``-bounded so a child that wedges during arm (never writes) degrades the
    posture instead of blocking the daemon. Returns whatever arrived (possibly empty on
    timeout or EOF)."""
    deadline = time.monotonic() + timeout
    buf = b""
    while len(buf) < 5:
        budget = deadline - time.monotonic()
        if budget <= 0:
            break
        readable, _, _ = select.select([fd], [], [], budget)
        if not readable:
            break  # timed out waiting for the child to arm
        chunk = os.read(fd, 5 - len(buf))
        if not chunk:
            break  # EOF: child wrote "F" and closed, or died
        buf += chunk
    return buf


def _proxy_env(loopback: str) -> dict[str, str]:
    """Child env overrides that route EVERY fetch path through the loopback chokepoint.

    ``JOBCRAWLER_EGRESS_PROXY`` covers ``build_live_ctx`` and the explicit ``Ctx(proxy=…)``
    param; ``HTTP(S)_PROXY``/``ALL_PROXY`` (+ empty ``NO_PROXY``) cover every ``trust_env``
    httpx client (the bare Ctx, the FlareSolverr solver). Both upper- and lower-case forms
    are set since httpx/httpcore read the lower-case names."""
    return {
        "JOBCRAWLER_EGRESS_PROXY": loopback,
        "HTTP_PROXY": loopback,
        "HTTPS_PROXY": loopback,
        "ALL_PROXY": loopback,
        "http_proxy": loopback,
        "https_proxy": loopback,
        "all_proxy": loopback,
        "NO_PROXY": "",
        "no_proxy": "",
    }
