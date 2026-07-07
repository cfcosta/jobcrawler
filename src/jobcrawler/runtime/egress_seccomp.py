"""The #19b firm rung: a rootless seccomp ``SECCOMP_RET_USER_NOTIF`` connect-broker.

This is the OS-enforcement plane :mod:`jobcrawler.runtime.egress` degrades away from
when it is unavailable. The child installs a small classic-BPF filter *in its own
``main``* (a user-notify filter must be installed by the task it governs, never in a
``preexec_fn``) that traps ``connect`` — plus the escape hatches around it — to the
trusted parent. The parent *broker* answers each trap:

  * ``connect`` to the pinned ``127.0.0.1:<proxy_port>`` loopback proxy → the broker
    opens **its own** socket to that proxy and injects it into the child at the child's
    socket fd (``SECCOMP_IOCTL_NOTIF_ADDFD`` with ``SETFD``), then completes the syscall
    with ``0`` (``NOTIF_SEND``). This is TOCTOU-free: the destination the child reaches
    is chosen by the broker, not re-read from child memory, so a second child thread
    swapping the sockaddr after the check cannot redirect it.
  * ``connect`` anywhere else, or a ``sendto`` carrying an explicit destination → denied
    ``EACCES``. So a raw socket a sandbox escape opens cannot reach a non-allowlisted
    host: its ``connect`` is refused and its only reachable peer is the allowlisting
    proxy, which itself screens the resolved IP (see :mod:`jobcrawler.runtime.egress`).

The filter also closes the ways to emit packets *without* ``connect``:

  * ``io_uring_setup``/``enter``/``register`` → ``ENOSYS`` (io_uring can issue its own
    ``connect``/``send`` off the syscall path the filter guards).
  * ``socket(AF_PACKET|AF_VSOCK|AF_ALG, …)`` → ``EAFNOSUPPORT`` (exotic families that
    sidestep the AF_INET egress model; AF_INET/AF_INET6/AF_UNIX/AF_NETLINK stay allowed
    — glibc startup and getaddrinfo need them).
  * a non-``x86_64`` ``arch`` (an i386/x32 compat syscall whose numbers differ) →
    ``ENOSYS``, so the numeric filter cannot be bypassed through the compat ABI.

**Residuals (documented, not closed here):** ``sendmsg``/``sendmmsg`` carrying a
destination is *not* trapped — the destination lives behind a double pointer classic
BPF cannot dereference, and trapping every ``sendmsg`` would break connected-socket
sends. So a raw *connectionless* ``sendmsg`` to an arbitrary host is a residual, as is
any UDP the proxy does not intermediate. These are low-bandwidth channels that require
an already-escaped socket, and #19a has already scrubbed the loot such a channel could
carry; the dominant channel (all TCP, which must ``connect``) is fully brokered.

Everything here is x86_64/Linux-specific and rootless-verified (uid 1000, no caps).
:func:`supported` gates it; where it returns ``False`` the egress ladder keeps the
portable data-plane proxy and logs a ``soft`` posture.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import platform
import socket
import struct
import sys
import threading

_log = logging.getLogger("jobcrawler.egress.seccomp")

# -- libc handle (guarded: absent libc / non-Linux → the rung is simply unsupported) --
try:
    _libc: ctypes.CDLL | None = ctypes.CDLL("libc.so.6", use_errno=True)
    _libc.syscall.restype = ctypes.c_long  # else a 64-bit ptr arg truncates to c_int
    _libc.ioctl.restype = ctypes.c_int
    # ioctl request numbers here exceed INT_MAX; a default c_int sign-extends them wrong
    _libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
    _libc.prctl.restype = ctypes.c_int
except OSError:  # pragma: no cover - only on a libc-less platform
    _libc = None

# -- x86_64 syscall numbers --
_NR_socket = 41
_NR_connect = 42
_NR_sendto = 44
_NR_seccomp = 317
_NR_io_uring_setup = 425
_NR_io_uring_enter = 426
_NR_io_uring_register = 427
_NR_pidfd_open = 434
_NR_pidfd_getfd = 438

# -- prctl / seccomp constants --
_PR_SET_NO_NEW_PRIVS = 38
_SET_MODE_FILTER = 1
_FLAG_NEW_LISTENER = 0x08
_RET_USER_NOTIF = 0x7FC00000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_ARCH_X86_64 = 0xC000003E

# -- classic-BPF opcodes --
_LD_ABS_W = 0x20  # BPF_LD | BPF_W | BPF_ABS
_JEQ_K = 0x15  # BPF_JMP | BPF_JEQ | BPF_K
_RET_K = 0x06  # BPF_RET | BPF_K

# -- seccomp_data field offsets (as the FILTER sees them, base 0) --
_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0_LO = 16  # args[0] low dword (little-endian): socket() domain
_OFF_ARG4_LO = 48  # args[4] low dword: sendto() dest_addr pointer, low half
_OFF_ARG4_HI = 52  # args[4] high dword

# -- socket() domains denied at creation --
_AF_PACKET = 17
_AF_VSOCK = 40
_AF_ALG = 38

# -- seccomp user-notify uapi: struct sizes + ioctl numbers --
# struct seccomp_notif { u64 id; u32 pid; u32 flags; seccomp_data data(64B) } = 80
_SIZEOF_NOTIF = 80
# struct seccomp_notif_resp { u64 id; s64 val; s32 error; u32 flags } = 24
_SIZEOF_RESP = 24
# struct seccomp_notif_addfd { u64 id; u32 flags; u32 srcfd; u32 newfd; u32 newfd_flags } = 24
_SIZEOF_ADDFD = 24
# seccomp_data nests at offset 16 inside seccomp_notif: nr@16, args[]@32
_NOTIF_OFF_NR = 16
_NOTIF_OFF_ARGS = 32
_IOCTL_RECV = 0xC0502100  # SECCOMP_IOCTL_NOTIF_RECV
_IOCTL_SEND = 0xC0182101  # SECCOMP_IOCTL_NOTIF_SEND
_IOCTL_ID_VALID = 0x40082102  # SECCOMP_IOCTL_NOTIF_ID_VALID
_IOCTL_ADDFD = 0x40182103  # SECCOMP_IOCTL_NOTIF_ADDFD
_ADDFD_FLAG_SETFD = 0x01  # install at the requested newfd (replace the child's sockfd)


def supported() -> bool:
    """``True`` iff this box can arm the firm rung: Linux, x86_64, libc reachable.

    The syscall/BPF numbers baked in above are x86_64-specific, so the rung is only
    offered there. Everywhere else the egress ladder keeps the portable proxy (``soft``).
    """
    return (
        _libc is not None
        and sys.platform.startswith("linux")
        and platform.machine() == "x86_64"
    )


# -- classic-BPF assembly ------------------------------------------------------


def _assemble(prog: list[tuple]) -> bytes:
    """Assemble a labelled classic-BPF program into packed ``sock_filter`` bytes.

    ``prog`` items are ``("L", name)`` label markers, ``(code, k)`` statements, or
    ``(code, k, jt, jf)`` jumps whose ``jt``/``jf`` are either a label name (resolved to
    a forward instruction offset) or an ``int`` offset (``0`` = fall through)."""
    labels: dict[str, int] = {}
    idx = 0
    for item in prog:
        if item[0] == "L":
            labels[item[1]] = idx
        else:
            idx += 1
    out: list[bytes] = []
    i = 0
    for item in prog:
        if item[0] == "L":
            continue
        if len(item) == 2:
            code, k = item
            out.append(struct.pack("HBBI", code, 0, 0, k & 0xFFFFFFFF))
        else:
            code, k, jt, jf = item
            jt_off = labels[jt] - i - 1 if isinstance(jt, str) else jt
            jf_off = labels[jf] - i - 1 if isinstance(jf, str) else jf
            if not (0 <= jt_off <= 255 and 0 <= jf_off <= 255):
                raise ValueError(f"bpf jump out of range at {i}: {jt_off},{jf_off}")
            out.append(struct.pack("HBBI", code, jt_off, jf_off, k & 0xFFFFFFFF))
        i += 1
    return b"".join(out)


def _build_connect_filter() -> bytes:
    """The firm-rung filter: trap ``connect`` (+ ``sendto`` w/ dest), fence the escapes."""
    enosys = _RET_ERRNO | (errno.ENOSYS & 0xFFFF)
    eaf = _RET_ERRNO | (errno.EAFNOSUPPORT & 0xFFFF)
    prog: list[tuple] = [
        # A non-x86_64 arch (i386/x32 compat) has different syscall numbers → refuse it
        # so the numeric checks below cannot be bypassed through the compat ABI.
        (_LD_ABS_W, _OFF_ARCH),
        (_JEQ_K, _ARCH_X86_64, "after_arch", "bad_arch"),
        ("L", "after_arch"),
        (_LD_ABS_W, _OFF_NR),
        (_JEQ_K, _NR_connect, "trap", 0),
        (_JEQ_K, _NR_sendto, "sendto", 0),
        (_JEQ_K, _NR_io_uring_setup, "enosys", 0),
        (_JEQ_K, _NR_io_uring_enter, "enosys", 0),
        (_JEQ_K, _NR_io_uring_register, "enosys", 0),
        (_JEQ_K, _NR_socket, "socket", "allow"),
        # sendto: allow a NULL dest_addr (a connected-socket send()); trap an explicit
        # destination (connectionless UDP the broker will refuse).
        ("L", "sendto"),
        (_LD_ABS_W, _OFF_ARG4_LO),
        (_JEQ_K, 0, "sendto_hi", "trap"),
        ("L", "sendto_hi"),
        (_LD_ABS_W, _OFF_ARG4_HI),
        (_JEQ_K, 0, "allow", "trap"),
        # socket(): deny the exotic families that sidestep the AF_INET egress model.
        ("L", "socket"),
        (_LD_ABS_W, _OFF_ARG0_LO),
        (_JEQ_K, _AF_PACKET, "eaf", 0),
        (_JEQ_K, _AF_VSOCK, "eaf", 0),
        (_JEQ_K, _AF_ALG, "eaf", "allow"),
        ("L", "trap"),
        (_RET_K, _RET_USER_NOTIF),
        ("L", "allow"),
        (_RET_K, _RET_ALLOW),
        ("L", "enosys"),
        (_RET_K, enosys),
        ("L", "bad_arch"),
        (_RET_K, enosys),
        ("L", "eaf"),
        (_RET_K, eaf),
    ]
    return _assemble(prog)


# -- child side: install the user-notify filter --------------------------------


def install_connect_listener() -> int:
    """Install the connect-broker filter in THIS process; return the listener fd.

    Rootless: ``PR_SET_NO_NEW_PRIVS`` then ``seccomp(SET_MODE_FILTER, NEW_LISTENER)``.
    Raises ``OSError`` if the kernel refuses (no seccomp, blocked by an outer sandbox);
    the caller degrades or refuses per policy. The returned fd is the child's handle to
    the notification queue — the parent copies it via :func:`attach_broker` and the child
    then closes its own copy so escaped code cannot self-answer its traps."""
    if _libc is None:  # pragma: no cover - guarded by supported()
        raise OSError("libc unavailable")
    if _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(NO_NEW_PRIVS)")
    prog = _build_connect_filter()
    buf = ctypes.create_string_buffer(prog, len(prog))
    fprog = struct.pack("HxxxxxxP", len(prog) // 8, ctypes.addressof(buf))
    fprog_buf = ctypes.create_string_buffer(fprog, len(fprog))
    fd = _libc.syscall(
        _NR_seccomp,
        _SET_MODE_FILTER,
        _FLAG_NEW_LISTENER,
        ctypes.c_void_p(ctypes.addressof(fprog_buf)),
    )
    # buf/fprog_buf stay alive until here; the kernel has copied the filter by now.
    if fd < 0:
        raise OSError(ctypes.get_errno(), "seccomp(SET_MODE_FILTER, NEW_LISTENER)")
    return int(fd)


# -- parent side: the broker ---------------------------------------------------


def _pidfd_open(pid: int) -> int:
    assert _libc is not None
    return int(_libc.syscall(_NR_pidfd_open, pid, 0))


def _pidfd_getfd(pidfd: int, targetfd: int) -> int:
    assert _libc is not None
    return int(_libc.syscall(_NR_pidfd_getfd, pidfd, targetfd, 0))


def _parse_sockaddr(raw: bytes) -> tuple[str, int] | None:
    """A connect() ``sockaddr`` → ``(ipv4, port)`` for AF_INET, else ``None``.

    Only AF_INET is recognised: the pinned proxy is a v4 literal, so anything else
    (v6, AF_UNIX, junk) is not the proxy and must be denied by the caller."""
    if len(raw) < 8:
        return None
    family = struct.unpack_from("H", raw, 0)[0]  # host-order sa_family
    if family != socket.AF_INET:
        return None
    port = struct.unpack_from("!H", raw, 2)[0]  # network byte order
    return socket.inet_ntoa(raw[4:8]), port


class Broker:
    """Parent-side connect-broker for one armed child (a daemon thread).

    Owns a copy of the child's ``NEW_LISTENER`` fd (via ``pidfd_getfd``). Each
    ``NOTIF_RECV`` is a trapped ``connect``/``sendto``: a ``connect`` to the pinned
    proxy is satisfied by injecting a broker-opened socket to that proxy; everything
    else is denied ``EACCES``. Never raises out of the thread — a mishandled trap is
    denied (fail closed) and the loop continues."""

    def __init__(self, listener_fd: int, proxy_addr: tuple[str, int]) -> None:
        self._fd = listener_fd
        self._proxy_host, self._proxy_port = proxy_addr
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="egress-broker", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        """Stop the broker and release the listener (unblocks a pending ``NOTIF_RECV``)."""
        self._stop.set()
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    # -- the loop --

    def _run(self) -> None:
        assert _libc is not None
        notif = bytearray(_SIZEOF_NOTIF)
        cbuf = (ctypes.c_char * _SIZEOF_NOTIF).from_buffer(notif)
        while not self._stop.is_set():
            notif[:] = b"\x00" * _SIZEOF_NOTIF  # kernel requires a zeroed struct per RECV
            ctypes.set_errno(0)
            rc = _libc.ioctl(self._fd, _IOCTL_RECV, cbuf)
            if rc < 0:
                if ctypes.get_errno() == errno.EINTR:
                    continue
                return  # child gone / listener closed → the broker is done
            try:
                self._dispatch(bytes(notif))
            except Exception:  # never let one trap crash the broker
                _log.exception("egress broker: dispatch failed")

    def _dispatch(self, raw: bytes) -> None:
        notif_id, pid, _flags = struct.unpack_from("QII", raw, 0)
        nr = struct.unpack_from("i", raw, _NOTIF_OFF_NR)[0]
        args = struct.unpack_from("6Q", raw, _NOTIF_OFF_ARGS)
        if not self._id_valid(notif_id):
            return  # the child died between trap and now; nothing to answer
        if nr == _NR_connect:
            self._broker_connect(notif_id, pid, args)
        else:  # a trapped sendto (explicit dest) or anything unexpected → refuse
            self._respond(notif_id, val=0, error=-errno.EACCES)

    def _broker_connect(
        self, notif_id: int, pid: int, args: tuple[int, ...]
    ) -> None:
        assert _libc is not None
        sockfd, addr_ptr, addrlen = int(args[0]), int(args[1]), int(args[2])
        dest = self._read_dest(pid, addr_ptr, addrlen)
        if dest != (self._proxy_host, self._proxy_port):
            self._respond(notif_id, val=0, error=-errno.EACCES)
            return
        # Allowed: a connect to our pinned loopback proxy. Open our own socket to it and
        # hand that to the child — the child never chooses the destination we dial.
        try:
            upstream = socket.create_connection(
                (self._proxy_host, self._proxy_port), timeout=5.0
            )
            upstream.setblocking(True)  # clear the timeout's O_NONBLOCK before injecting
        except OSError:
            self._respond(notif_id, val=0, error=-errno.ECONNREFUSED)
            return
        if not self._id_valid(notif_id):  # child vanished while we dialed
            upstream.close()
            return
        addfd = struct.pack(
            "QIIII", notif_id, _ADDFD_FLAG_SETFD, upstream.fileno(), sockfd, 0
        )
        rc = _libc.ioctl(
            self._fd,
            _IOCTL_ADDFD,
            (ctypes.c_char * _SIZEOF_ADDFD).from_buffer_copy(addfd),
        )
        if rc < 0:
            upstream.close()
            self._respond(notif_id, val=0, error=-errno.EACCES)
            return
        upstream.detach()  # the child owns the fd now (installed at sockfd via SETFD)
        # ADDFD installs the fd but leaves connect() pending; complete it with success.
        self._respond(notif_id, val=0, error=0)

    def _read_dest(
        self, pid: int, addr_ptr: int, addrlen: int
    ) -> tuple[str, int] | None:
        if not (0 < addrlen <= 128):  # a sockaddr is small; bound the /proc read
            return None
        try:
            with open(f"/proc/{pid}/mem", "rb", 0) as mem:
                mem.seek(addr_ptr)
                raw = mem.read(addrlen)
        except OSError:
            return None
        if len(raw) != addrlen:
            return None
        return _parse_sockaddr(raw)

    def _id_valid(self, notif_id: int) -> bool:
        assert _libc is not None
        idbuf = (ctypes.c_char * 8).from_buffer_copy(struct.pack("Q", notif_id))
        return _libc.ioctl(self._fd, _IOCTL_ID_VALID, idbuf) == 0

    def _respond(self, notif_id: int, *, val: int, error: int) -> None:
        assert _libc is not None
        resp = struct.pack("QqiI", notif_id, val, error, 0)
        _libc.ioctl(
            self._fd, _IOCTL_SEND, (ctypes.c_char * _SIZEOF_RESP).from_buffer_copy(resp)
        )


def attach_broker(
    child_pid: int, child_listener_fd: int, proxy_addr: tuple[str, int]
) -> Broker | None:
    """Copy the child's listener fd into the parent and start a :class:`Broker`.

    ``pidfd_open(child)`` + ``pidfd_getfd(child_listener_fd)`` duplicates the child's
    ``NEW_LISTENER`` fd here (the child keeps its own copy open until we return). Returns
    the started broker, or ``None`` if either step fails — the caller then aborts the
    child (fail closed) rather than run it with an un-brokered filter."""
    if _libc is None:  # pragma: no cover - guarded by supported()
        return None
    pidfd = _pidfd_open(child_pid)
    if pidfd < 0:
        return None
    try:
        listener = _pidfd_getfd(pidfd, child_listener_fd)
    finally:
        try:
            os.close(pidfd)
        except OSError:
            pass
    if listener < 0:
        return None
    broker = Broker(listener, proxy_addr)
    broker.start()
    return broker
