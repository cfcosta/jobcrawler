"""Tests for the `autocrawl optimize` offline job wiring (§4.11).

The live compile path (LM + Deno + sandbox) is not unit-tested; the cheap guards
and the stability-run accounting are. The command must no-op cleanly when there is
no admitted trainset yet — the always-valid M5a behavior on too little data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from bizup_autocrawler.cli_optimize import _stable_runs, optimize_command
from bizup_autocrawler.state import StateStore


def _finish(store: StateStore, slug: str, items: int) -> None:
    run_id = store.start_run(slug)
    store.finish_run(
        run_id, items_found=items, new_items=items, errors=0, pages_fetched=1
    )


def test_optimize_no_ops_without_an_admitted_trainset(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTOCRAWLER_TRAINSET_PATH", str(tmp_path / "trainset.jsonl"))
    monkeypatch.setenv("AUTOCRAWLER_STATE_URL", f"sqlite:///{tmp_path}/state.db")

    rc = optimize_command(argparse.Namespace())

    assert rc == 0  # no LM, no compile — just the cheap guard
    assert "no admitted trainset" in capsys.readouterr().out


def test_stable_runs_counts_consecutive_healthy_runs() -> None:
    store = StateStore(":memory:")
    _finish(store, "s", 5)
    _finish(store, "s", 8)
    assert _stable_runs(store, "s") == 2

    _finish(store, "s", 0)  # a zero-items run = stale → breaks the streak (§4.8)
    assert _stable_runs(store, "s") == 0
    store.close()
