"""Tests for the out-of-band, single-flight optimize dispatch (§4.11).

The lock is an ``fcntl.flock`` advisory lock keyed on the open file *description*, so
on Linux a second ``open()`` of the same file — even from the same process — contends
with the first (``man 2 flock``: "file descriptors [from separate open()s] are treated
independently"). That lets these tests exercise real lock contention in-process without
spawning a second interpreter; the production case (a detached child vs. the daemon) has
the same description-vs-description semantics.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jobcrawler import config, optimize_job


def test_unheld_lock_is_not_locked(tmp_path: Path) -> None:
    lock = str(tmp_path / "optimize.lock")
    assert optimize_job.is_locked(lock) is False


def test_is_locked_is_true_while_held(tmp_path: Path) -> None:
    lock = str(tmp_path / "optimize.lock")
    with optimize_job.optimize_lock(lock):
        assert optimize_job.is_locked(lock) is True
    assert optimize_job.is_locked(lock) is False  # released on exit


def test_a_second_acquire_raises_already_running(tmp_path: Path) -> None:
    lock = str(tmp_path / "optimize.lock")
    with optimize_job.optimize_lock(lock):
        with pytest.raises(optimize_job.AlreadyRunning):
            with optimize_job.optimize_lock(lock):
                pass


def test_lock_is_reacquirable_after_release(tmp_path: Path) -> None:
    lock = str(tmp_path / "optimize.lock")
    with optimize_job.optimize_lock(lock):
        pass
    with optimize_job.optimize_lock(lock):  # must not raise
        pass


def test_lock_releases_even_on_exception(tmp_path: Path) -> None:
    lock = str(tmp_path / "optimize.lock")
    with pytest.raises(ValueError):
        with optimize_job.optimize_lock(lock):
            raise ValueError("boom")
    # A crash mid-block must not strand the lock.
    with optimize_job.optimize_lock(lock):
        pass


def test_probe_does_not_strand_the_lock(tmp_path: Path) -> None:
    # is_locked briefly takes and releases the lock when free; that must not leave it
    # held (a probe is read-only intent).
    lock = str(tmp_path / "optimize.lock")
    optimize_job.is_locked(lock)
    optimize_job.is_locked(lock)
    with optimize_job.optimize_lock(lock):  # still acquirable after two probes
        pass


# -- dispatch (detached subprocess) ------------------------------------------


class _FakePopen:
    instances: list[_FakePopen] = []

    def __init__(self, cmd: list[str], **kwargs: object) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        _FakePopen.instances.append(self)


@pytest.fixture(autouse=True)
def _reset_fake_popen() -> None:
    _FakePopen.instances = []


def test_dispatch_spawns_detached_module_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = str(tmp_path / "optimize.lock")
    logf = str(tmp_path / "optimize.log")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    assert optimize_job.dispatch_optimize(lock_path=lock, log_path=logf) is True

    assert len(_FakePopen.instances) == 1
    spawned = _FakePopen.instances[0]
    # Invoked as `python -m jobcrawler optimize` — PATH-independent. Manual
    # default is opt-in: no --adopt (write + deploy hint, no live pointer flip).
    assert spawned.cmd[0] == sys.executable
    assert spawned.cmd[1:] == ["-m", "jobcrawler", "optimize"]
    # Detached so it survives a daemon restart; no stdin; output captured.
    assert spawned.kwargs["start_new_session"] is True
    assert spawned.kwargs["stdin"] is subprocess.DEVNULL
    assert spawned.kwargs["stderr"] is subprocess.STDOUT
    # Path hardening: child pinned to the daemon's cwd so it resolves the same
    # CWD-relative trainset / generators / lock / log files (no second concurrent run).
    assert spawned.kwargs["cwd"] == os.getcwd()
    assert Path(logf).exists()  # log opened for append


def test_dispatch_adopt_appends_the_adopt_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The autonomous reopt path dispatches with adopt=True so a margin-clearing
    # generator flips the live pointer with no restart ("full auto, gated by proof").
    lock = str(tmp_path / "optimize.lock")
    logf = str(tmp_path / "optimize.log")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    assert (
        optimize_job.dispatch_optimize(adopt=True, lock_path=lock, log_path=logf)
        is True
    )

    spawned = _FakePopen.instances[0]
    assert spawned.cmd[1:] == ["-m", "jobcrawler", "optimize", "--adopt"]


def test_dispatch_skips_when_a_job_is_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = str(tmp_path / "optimize.lock")
    logf = str(tmp_path / "optimize.log")
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    with optimize_job.optimize_lock(lock):
        assert optimize_job.dispatch_optimize(lock_path=lock, log_path=logf) is False
    assert _FakePopen.instances == []  # no child spawned while one is running


def test_dispatch_uses_config_paths_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = str(tmp_path / "cfg.lock")
    logf = str(tmp_path / "cfg.log")
    monkeypatch.setattr(config, "optimize_lock_path", lambda: lock)
    monkeypatch.setattr(config, "optimize_log_path", lambda: logf)
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)

    assert optimize_job.dispatch_optimize() is True
    assert Path(logf).exists()


# -- the optimize command is single-flight -----------------------------------


def test_optimize_command_runs_when_lock_is_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobcrawler import cli_optimize

    lock = str(tmp_path / "optimize.lock")
    monkeypatch.setattr(config, "optimize_lock_path", lambda: lock)
    ran: list[int] = []
    monkeypatch.setattr(cli_optimize, "_run_optimize", lambda args: ran.append(1) or 0)

    assert cli_optimize.optimize_command(argparse.Namespace()) == 0
    assert ran == [1]


def test_optimize_command_exits_cleanly_when_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jobcrawler import cli_optimize

    lock = str(tmp_path / "optimize.lock")
    monkeypatch.setattr(config, "optimize_lock_path", lambda: lock)
    ran: list[int] = []
    monkeypatch.setattr(cli_optimize, "_run_optimize", lambda args: ran.append(1) or 0)

    with optimize_job.optimize_lock(lock):  # a job is already holding the lock
        assert cli_optimize.optimize_command(argparse.Namespace()) == 0
    assert ran == []  # _run_optimize never invoked under contention
