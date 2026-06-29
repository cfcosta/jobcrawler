"""Tests for the mutable active-generator pointer (§4.11 auto-adoption + rollback).

The pointer is a small ``ACTIVE`` file (plus an ``ACTIVE.history`` LIFO stack) in the
generator dir; auto-adoption writes it and ``rollback-generator`` undoes the last
write — all the way back to baseline when the first-ever adoption is undone. These
tests pin the read/write contract and, especially, that the history stack unwinds
adoptions in true LIFO order (the property the auto-rollback watcher relies on).

Every test points :func:`config.generator_dir` at a fresh ``tmp_path`` so the pointer
lives in an isolated dir and never touches a real generators/ tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from bizup_autocrawler import config, generator_pointer


@pytest.fixture(autouse=True)
def _isolated_generator_dir(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))


def test_active_is_none_when_unset() -> None:
    assert generator_pointer.active_generator_path() is None


def test_set_active_then_read_round_trips() -> None:
    generator_pointer.set_active("generators/generator-1.json")
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"


def test_set_active_writes_atomically_without_leaving_a_temp(tmp_path: Path) -> None:
    generator_pointer.set_active("generators/generator-1.json")
    # The temp file used for the atomic os.replace must not survive the write.
    assert not (tmp_path / "ACTIVE.tmp").exists()
    assert (tmp_path / "ACTIVE").exists()


def test_clear_active_removes_the_pointer() -> None:
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.clear_active()
    assert generator_pointer.active_generator_path() is None


def test_clear_active_is_idempotent() -> None:
    # Clearing an already-absent pointer is a no-op, not a FileNotFoundError.
    generator_pointer.clear_active()
    generator_pointer.clear_active()
    assert generator_pointer.active_generator_path() is None


def test_blank_pointer_reads_as_unset(tmp_path: Path) -> None:
    # A blank/whitespace ACTIVE file means "baseline", same as absent.
    (tmp_path / "ACTIVE").write_text("  \n")
    assert generator_pointer.active_generator_path() is None


def test_rollback_with_no_history_reports_nothing_to_undo() -> None:
    assert generator_pointer.rollback_active() == (False, None)


def test_rollback_of_the_first_adoption_returns_to_baseline() -> None:
    generator_pointer.set_active("generators/generator-1.json")

    ok, restored = generator_pointer.rollback_active()

    # First-ever adoption rolled back ⇒ baseline: pointer cleared, restored is None.
    assert ok is True
    assert restored is None
    assert generator_pointer.active_generator_path() is None


def test_rollback_unwinds_adoptions_in_lifo_order() -> None:
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.set_active("generators/generator-2.json")
    generator_pointer.set_active("generators/generator-3.json")
    assert generator_pointer.active_generator_path() == "generators/generator-3.json"

    # Each rollback undoes exactly one adoption, newest first...
    assert generator_pointer.rollback_active() == (True, "generators/generator-2.json")
    assert generator_pointer.active_generator_path() == "generators/generator-2.json"
    assert generator_pointer.rollback_active() == (True, "generators/generator-1.json")
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"
    # ...down to baseline...
    assert generator_pointer.rollback_active() == (True, None)
    assert generator_pointer.active_generator_path() is None
    # ...and then there is nothing left to undo.
    assert generator_pointer.rollback_active() == (False, None)


def test_adopt_after_rollback_repushes_history() -> None:
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.rollback_active()  # back to baseline

    generator_pointer.set_active("generators/generator-2.json")
    assert generator_pointer.active_generator_path() == "generators/generator-2.json"

    # The fresh adoption recorded baseline as its prior, so rollback returns there.
    ok, restored = generator_pointer.rollback_active()
    assert ok is True
    assert restored is None
    assert generator_pointer.active_generator_path() is None
