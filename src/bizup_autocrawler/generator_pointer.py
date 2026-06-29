"""The mutable active-generator pointer (§4.11 auto-adoption + versioned rollback).

``autocrawl optimize`` writes versioned ``generator-N.json`` artifacts, but the live
process needs to know *which* one is active — and to change it **without a restart** so
the autonomous loop can adopt a freshly-promoted generator and roll a regressing one
back on its own. An env var (``AUTOCRAWLER_GENERATOR_PATH``) cannot be changed under a
running daemon; this pointer file can, and :func:`load_generator` re-reads it on each
boot / regeneration.

The pointer is a small ``ACTIVE`` file in the generator dir holding one path (absent or
blank ⇒ baseline). :func:`set_active` is atomic (temp + ``os.replace``) and pushes the
prior value onto an ``ACTIVE.history`` stack, so :func:`rollback_active` is a true undo
of the last adoption — including all the way back to baseline when the first-ever
adoption is undone. The env var is kept as the *fallback* (resolution order
arg > pointer > env > baseline), so existing systemd deploys that only set
``AUTOCRAWLER_GENERATOR_PATH`` and never adopt still boot from it.

Adoptions are serialized by the §4.11 optimize single-flight lock (only the optimize
job calls :func:`set_active`), so the read-then-push in :func:`set_active` is not racy
against another adoption; per-file atomic writes keep an operator ``rollback-generator``
issued mid-optimize from corrupting either file.
"""

from __future__ import annotations

import os
from pathlib import Path

from bizup_autocrawler import config

_ACTIVE = "ACTIVE"
_HISTORY = "ACTIVE.history"


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


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)  # atomic on POSIX within one filesystem


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
    adoption to undo. Used by ``autocrawl rollback-generator`` and the P4 auto-rollback
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
