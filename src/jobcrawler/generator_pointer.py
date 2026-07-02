"""The mutable active-generator pointer (§4.11 auto-adoption + versioned rollback).

``jobcrawl optimize`` writes versioned ``generator-N.json`` artifacts, but the live
process needs to know *which* one is active — and to change it **without a restart** so
the autonomous loop can adopt a freshly-promoted generator and roll a regressing one
back on its own. An env var (``JOBCRAWLER_GENERATOR_PATH``) cannot be changed under a
running daemon; this pointer file can, and :func:`load_generator` re-reads it on each
boot / regeneration.

The pointer is a small ``ACTIVE`` file in the generator dir holding one path (absent or
blank ⇒ baseline). :func:`set_active` is atomic (temp + ``os.replace``) and pushes the
prior value onto an ``ACTIVE.history`` stack, so :func:`rollback_active` is a true undo
of the last adoption — including all the way back to baseline when the first-ever
adoption is undone. The env var is kept as the *fallback* (resolution order
arg > pointer > env > baseline), so existing systemd deploys that only set
``JOBCRAWLER_GENERATOR_PATH`` and never adopt still boot from it.

Adoptions are serialized by the §4.11 optimize single-flight lock (only the optimize
job calls :func:`set_active`), so the read-then-push in :func:`set_active` is not racy
against another adoption; per-file atomic writes keep an operator ``rollback-generator``
issued mid-optimize from corrupting either file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jobcrawler import config

_ACTIVE = "ACTIVE"
_HISTORY = "ACTIVE.history"
_ADOPTION = "ADOPTION"


def _active_file() -> Path:
    return Path(config.generator_dir()) / _ACTIVE


def _history_file() -> Path:
    return Path(config.generator_dir()) / _HISTORY


def active_generator_path() -> str | None:
    """The path the active-generator pointer names, or None if unset (⇒ baseline)."""
    try:
        raw = _active_file().read_text().strip()
    except OSError:
        return None
    return raw or None


def active_generator_meta() -> dict[str, object]:
    """The active generator's provenance sidecar (held-out score, snapshot…), or ``{}``.

    Reads the ``<stem>.meta.json`` sidecar that
    :func:`jobcrawler.generate.generator.save_generator` writes — but inline
    here (the convention is one line) rather than importing that dspy-heavy module, so
    the operator ``status`` surface that calls this stays fast and dependency-light.
    ``{}`` for baseline (no pointer) or a missing/corrupt sidecar, so the surface
    degrades to "n/a" rather than crashing the read."""
    active = active_generator_path()
    if active is None:
        return {}
    p = Path(active)
    meta = p.with_name(f"{p.stem}.meta.json")
    try:
        data = json.loads(meta.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    # Unique temp in the same dir (same filesystem ⇒ os.replace is atomic) rather than
    # a fixed ``<name>.tmp``: two concurrent writers to the same pointer file — an
    # operator ``rollback-generator`` racing the optimize job's adoption — must not
    # collide on one shared temp and corrupt or lose a write. With distinct temps the
    # replaces simply resolve last-wins; neither file is ever seen torn.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp, path)  # atomic on POSIX within one filesystem
    except BaseException:
        tmp.unlink(missing_ok=True)  # never leak the temp on a failed write
        raise


def set_active(path: str | Path) -> None:
    """Point the active generator at ``path`` atomically, recording the prior value.

    The prior pointer (or a blank line ⇒ baseline) is pushed onto the history stack so
    :func:`rollback_active` can undo this adoption. Used by ``optimize --adopt``, which
    passes the ``Path`` straight from :func:`save_generator`.
    """
    prior = active_generator_path()
    _push_history(prior if prior is not None else "")
    _atomic_write(_active_file(), str(path))


def clear_active() -> None:
    """Remove the pointer so resolution falls back to the env var / baseline."""
    try:
        _active_file().unlink()
    except FileNotFoundError:
        pass


def _read_history() -> list[str]:
    try:
        return _history_file().read_text().splitlines()
    except OSError:
        return []


def _push_history(value: str) -> None:
    history = _read_history()
    history.append(value)
    _atomic_write(_history_file(), "\n".join(history) + "\n")


def _pop_history() -> str | None:
    history = _read_history()
    if not history:
        return None
    value = history.pop()
    if history:
        _atomic_write(_history_file(), "\n".join(history) + "\n")
    else:
        try:
            _history_file().unlink()
        except FileNotFoundError:
            pass
    return value


def rollback_active() -> tuple[bool, str | None]:
    """Undo the last adoption: restore the prior pointer (or baseline).

    Returns ``(True, restored)`` where ``restored`` is the path now active (``None`` if
    rolled all the way back to baseline), or ``(False, None)`` if there is no prior
    adoption to undo. Used by ``jobcrawl rollback-generator`` and the P4 auto-rollback
    watcher.
    """
    prev = _pop_history()
    if prev is None:
        return (False, None)
    if prev == "":
        clear_active()
        return (True, None)
    _atomic_write(_active_file(), prev)
    return (True, prev)


# --- adoption-under-probation marker (§4.11 P4c) ---------------------------
# A sibling ``ADOPTION`` file recording the *latest* adoption and when it went live,
# so the post-adoption regression watcher can wait out a probation window and then
# judge it. Distinct from the pointer/history (which track *which* generator is live):
# this is a one-shot probation record the watcher consumes once it renders a verdict.


@dataclass(frozen=True, slots=True)
class AdoptionMarker:
    """A generator adoption under probation: which artifact, and when it went live.

    Written by ``optimize --adopt`` right after it flips the pointer; read back by the
    §4.11 regression watcher, which compares fleet health before vs after ``adopted_at``
    once the probation window elapses, then clears the marker.
    """

    path: str
    adopted_at: datetime


def _adoption_file() -> Path:
    return Path(config.generator_dir()) / _ADOPTION


def record_adoption(path: str | Path, *, at: datetime) -> None:
    """Mark ``path`` as a generator adoption under probation as of ``at`` (§4.11 P4c).

    Atomic, and overwrites any prior marker: adoptions are serialized by the optimize
    single-flight lock, so the latest is the only one under probation. The §4.11
    rollback watcher reads this after the probation window to judge the adoption.
    """
    payload = json.dumps({"path": str(path), "adopted_at": at.isoformat()})
    _atomic_write(_adoption_file(), payload)


def read_adoption() -> AdoptionMarker | None:
    """The current adoption-under-probation marker, or None when there is none.

    None on a missing, blank, or corrupt marker — a hand-truncated file must leave the
    watcher with simply nothing to judge rather than crashing the run loop.
    """
    try:
        raw = _adoption_file().read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return AdoptionMarker(
            path=str(data["path"]),
            adopted_at=datetime.fromisoformat(data["adopted_at"]),
        )
    except (ValueError, KeyError, TypeError):
        return None


def clear_adoption() -> None:
    """Consume the adoption marker once the watcher has rendered a verdict (§4.11)."""
    try:
        _adoption_file().unlink()
    except FileNotFoundError:
        pass
