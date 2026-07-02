"""Out-of-band, single-flight dispatch of the offline §4.11 optimize job.

The GEPA compile is multi-hour and must **never** run in-band in the §4.9 scheduler
loop (a tick blocked for hours stops all crawling). This module hands it off as a
*detached* ``autocrawl optimize`` subprocess and guards it with a file lock so two
compiles never run at once — a slow reopt timer that fires while a previous run is
still going is a no-op, not a pile-up of concurrent multi-hour runs all fighting over
the same trainset and generator dir.

The lock is an OS advisory lock (``fcntl.flock``) held for the lifetime of the optimize
process, not a PID file: a crash releases it automatically, so there is no stale lock
to reap. ``flock`` locks ride on the open file *description* (not the process), so a
probe that opens its own fd and releases cannot disturb a real holder — which is why
this uses ``flock`` rather than ``fcntl`` record locks (those drop on any ``close``).

Three entry points:

  * :func:`optimize_lock` — the context manager the optimize command itself holds for
    its whole run (the authoritative single-flight guard);
  * :func:`is_locked` — a non-blocking probe the reopt timer uses to skip dispatch when
    a job is already running;
  * :func:`dispatch_optimize` — spawn the detached subprocess (never waits on it).
"""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from bizup_autocrawler import config

_log = logging.getLogger("bizup_autocrawler.optimize_job")


class AlreadyRunning(RuntimeError):
    """Raised by :func:`optimize_lock` when another process already holds the lock."""


@contextmanager
def optimize_lock(path: str | None = None) -> Iterator[None]:
    """Hold the single-flight optimize lock for the duration of the ``with`` block.

    Takes an exclusive, *non-blocking* advisory lock on the lockfile. The lock lives
    with the open fd, so a crash mid-compile releases it (no stale lock to reap). If
    another process already holds it, raises :class:`AlreadyRunning` rather than
    queueing — the offline compile is multi-hour, so a second concurrent run is never
    wanted. Both the manual ``autocrawl optimize`` and the scheduler's detached
    dispatch acquire this, so they share one single-flight.
    """
    lock_path = path or config.optimize_lock_path()
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(
                f"another optimize job already holds {lock_path!r}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_locked(path: str | None = None) -> bool:
    """True if an optimize job currently holds the lock (a non-blocking probe).

    Opens the lockfile on a fresh fd and tries the exclusive lock without blocking; a
    failure means the lock is held elsewhere. Any lock the probe itself takes is
    released immediately, so probing never blocks a real job. This is only an
    optimization to avoid spawning a doomed child — the dispatched job re-acquires the
    lock itself, which is the authoritative guard, so a probe TOCTOU is harmless.
    """
    lock_path = path or config.optimize_lock_path()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True  # held elsewhere
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def dispatch_optimize(
    *, adopt: bool = False, lock_path: str | None = None, log_path: str | None = None
) -> bool:
    """Spawn ``autocrawl optimize`` as a detached, out-of-band subprocess (§4.11).

    ``adopt`` appends ``--adopt`` so a promoted generator flips the live pointer with no
    restart — the path the §4.9 reopt timer takes ("full auto, gated by proof"); the
    manual default is opt-in (write + deploy hint, no auto-adopt).

    Returns True if a job was launched, False if one is already running (the lock is
    held) — a reopt tick that fires while a previous compile is still going is a no-op.
    The child is started in its own session (``start_new_session``) so it survives a
    daemon restart/stop, inherits the daemon's environment (the AUTOCRAWLER_* config),
    and re-acquires the lock itself. Its cwd is pinned to the daemon's current directory
    (``cwd=os.getcwd()``) so the CWD-relative trainset / generators / lock / log paths
    resolve to the *same* files the daemon probed — the child can't drift onto a
    different lockfile and run a second concurrent compile. Its stdout/stderr append to
    the optimize log; this never waits on the child.
    """
    if is_locked(lock_path):
        _log.info("optimize already running; skipping dispatch")
        return False
    log_file = log_path or config.optimize_log_path()
    cmd = [sys.executable, "-m", "bizup_autocrawler", "optimize"]
    if adopt:
        cmd.append("--adopt")
    # Line-buffered append so the child's progress is followable; the child inherits its
    # own dup of this fd, so closing the parent's copy below does not affect it.
    out = open(log_file, "a", buffering=1)
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            cwd=os.getcwd(),  # path hardening: child resolves the same CWD-relative paths
        )
    finally:
        out.close()
    _log.info(
        "dispatched out-of-band optimize%s -> %s", " --adopt" if adopt else "", log_file
    )
    return True
