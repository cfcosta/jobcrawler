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

from datetime import datetime, timezone
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
    # The unique temp used for the atomic os.replace must not survive the write — and
    # no temp of *any* name (the write uses mkstemp, not a fixed ``ACTIVE.tmp``, so a
    # concurrent rollback/adopt can't collide on one shared temp).
    assert not (tmp_path / "ACTIVE.tmp").exists()
    assert list(tmp_path.glob("*.tmp")) == []
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


# -- adoption-under-probation marker (§4.11 P4c) -----------------------------


_T = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def test_adoption_is_none_until_recorded() -> None:
    assert generator_pointer.read_adoption() is None


def test_record_then_read_adoption_round_trips() -> None:
    generator_pointer.record_adoption("generators/generator-2.json", at=_T)
    marker = generator_pointer.read_adoption()
    assert marker is not None
    assert marker.path == "generators/generator-2.json"
    assert marker.adopted_at == _T


def test_record_adoption_writes_atomically_without_leaving_a_temp(
    tmp_path: Path,
) -> None:
    generator_pointer.record_adoption("generators/generator-1.json", at=_T)
    assert not (tmp_path / "ADOPTION.tmp").exists()
    assert (tmp_path / "ADOPTION").exists()


def test_record_adoption_overwrites_a_prior_marker() -> None:
    # Adoptions are serialized by the optimize lock, so the latest is the only one under
    # probation — a second record replaces the first wholesale.
    generator_pointer.record_adoption("generators/generator-1.json", at=_T)
    later = datetime(2026, 1, 12, tzinfo=timezone.utc)
    generator_pointer.record_adoption("generators/generator-2.json", at=later)
    marker = generator_pointer.read_adoption()
    assert marker is not None
    assert marker.path == "generators/generator-2.json"
    assert marker.adopted_at == later


def test_clear_adoption_consumes_the_marker() -> None:
    generator_pointer.record_adoption("generators/generator-1.json", at=_T)
    generator_pointer.clear_adoption()
    assert generator_pointer.read_adoption() is None


def test_clear_adoption_is_idempotent() -> None:
    generator_pointer.clear_adoption()
    generator_pointer.clear_adoption()
    assert generator_pointer.read_adoption() is None


def test_corrupt_adoption_marker_reads_as_none(tmp_path: Path) -> None:
    # A hand-truncated / non-JSON marker must leave the watcher with nothing to judge,
    # not crash the run loop.
    (tmp_path / "ADOPTION").write_text("{not json")
    assert generator_pointer.read_adoption() is None


def test_adoption_marker_missing_field_reads_as_none(tmp_path: Path) -> None:
    (tmp_path / "ADOPTION").write_text('{"path": "generators/generator-1.json"}')
    assert generator_pointer.read_adoption() is None  # no adopted_at → unusable


def test_adoption_marker_is_independent_of_the_pointer() -> None:
    # The probation marker and the live pointer are separate files: clearing one leaves
    # the other intact (the watcher consumes the marker without touching the pointer).
    generator_pointer.set_active("generators/generator-1.json")
    generator_pointer.record_adoption("generators/generator-1.json", at=_T)
    generator_pointer.clear_adoption()
    assert generator_pointer.read_adoption() is None
    assert generator_pointer.active_generator_path() == "generators/generator-1.json"


# -- active_generator_meta: the dspy-free provenance reader for `status` (P4d) ----


def test_active_generator_meta_is_empty_at_baseline() -> None:
    # No pointer ⇒ baseline ⇒ no provenance to read (the status surface shows "n/a").
    assert generator_pointer.active_generator_meta() == {}


def test_active_generator_meta_reads_the_sidecar(tmp_path: Path) -> None:
    # Mirrors save_generator's "<stem>.meta.json" sidecar, read without importing the
    # dspy-heavy generator module so `status` stays fast.
    (tmp_path / "generator-3.json").write_text("{}")
    (tmp_path / "generator-3.meta.json").write_text(
        '{"heldout_score": 2.45, "trainset_snapshot": 7}'
    )
    generator_pointer.set_active(str(tmp_path / "generator-3.json"))

    meta = generator_pointer.active_generator_meta()

    assert meta["heldout_score"] == 2.45
    assert meta["trainset_snapshot"] == 7


def test_active_generator_meta_is_empty_when_sidecar_missing(tmp_path: Path) -> None:
    # An active generator with no sidecar (e.g. a hand-placed artifact) degrades to {}.
    generator_pointer.set_active(str(tmp_path / "generator-3.json"))
    assert generator_pointer.active_generator_meta() == {}


def test_active_generator_meta_is_empty_when_sidecar_corrupt(tmp_path: Path) -> None:
    (tmp_path / "generator-3.meta.json").write_text("{not json")
    generator_pointer.set_active(str(tmp_path / "generator-3.json"))
    assert generator_pointer.active_generator_meta() == {}
