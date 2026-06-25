"""Tests for the `autocrawl optimize` offline job wiring (§4.11).

The live compile path (LM + Deno + sandbox) is not unit-tested; the cheap guards
and the stability-run accounting are. The command must no-op cleanly when there is
no admitted trainset yet — the always-valid M5a behavior on too little data.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

import dspy
import pytest
from pytest import CaptureFixture, MonkeyPatch

from bizup_autocrawler.cli_optimize import (
    _build_factory,
    _split_sizes,
    _stable_runs,
    optimize_command,
)
from bizup_autocrawler.generate.generator import CrawlerGenerator
from bizup_autocrawler.state import StateStore


def _ex(site: str) -> dspy.Example:
    return dspy.Example(site_url=f"https://{site}.com").with_inputs("site_url")


def _factory(optimizer: str, train: list[dspy.Example] | None = None):
    """Build an optimizer factory with opaque (faked-downstream) compile inputs."""
    return _build_factory(
        optimizer,
        baseline=cast("CrawlerGenerator", "BASE"),
        train=train if train is not None else [_ex("a")],
        val=[_ex("b")],
        bootstrap_metric=lambda *a: 0.0,
        eval_metric=lambda *a: 0,
        max_tokens=100,
    )


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


# -- _split_sizes: scale (holdout, val) with the pool -------------------------


def test_split_sizes_at_floor_is_one_one() -> None:
    assert _split_sizes(3) == (1, 1)  # matches the original fixed split


def test_split_sizes_scales_with_pool() -> None:
    assert _split_sizes(10) == (2, 2)
    assert _split_sizes(20) == (4, 4)


def test_split_sizes_always_leaves_train_nonempty() -> None:
    for n in range(3, 60):
        holdout, val = _split_sizes(n)
        assert holdout >= 1 and val >= 1
        assert holdout + val < n  # train gets >= 1 site


# -- _build_factory: the optimizer-ladder dispatch (Fix 1, wiring GEPA) -------


def test_build_factory_bootstrap_rung_calls_bootstrap_compile(
    monkeypatch: MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def fake_bootstrap(baseline: object, **kwargs: object) -> str:
        seen["which"] = "bootstrap"
        return "BOOT"

    monkeypatch.setattr(
        "bizup_autocrawler.cli_optimize.bootstrap_compile", fake_bootstrap
    )
    factory = _factory("bootstrap_fewshot")
    assert factory() == "BOOT"
    assert seen["which"] == "bootstrap"


def test_build_factory_gepa_rung_calls_gepa_compile(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    seen: dict[str, str] = {}

    def fake_gepa(baseline: object, **kwargs: object) -> str:
        seen["which"] = "gepa"
        return "GEPA"

    monkeypatch.setattr("bizup_autocrawler.cli_optimize.gepa_compile", fake_gepa)
    factory = _factory("gepa")
    assert factory() == "GEPA"
    assert seen["which"] == "gepa"


def test_build_factory_gepa_rung_requires_reflection_model_eagerly(
    monkeypatch: MonkeyPatch,
) -> None:
    # The reflection model is validated when the factory is *built* (before the
    # expensive minibatch scoring), not deferred into the compile.
    monkeypatch.delenv("AUTOCRAWLER_REFLECTION_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="AUTOCRAWLER_REFLECTION_MODEL"):
        _factory("gepa")


def test_build_factory_mipro_falls_forward_to_gepa_with_notice(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    # MIPRO is documented-not-built in v1 → use the strongest built rung (GEPA),
    # loudly — never a silent drop to bootstrap.
    monkeypatch.setenv("AUTOCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    monkeypatch.setattr(
        "bizup_autocrawler.cli_optimize.gepa_compile", lambda *a, **k: "GEPA"
    )
    monkeypatch.setattr(
        "bizup_autocrawler.cli_optimize.bootstrap_compile",
        lambda *a, **k: pytest.fail("mipro must not fall through to bootstrap"),
    )
    factory = _factory("mipro", train=[_ex(f"t{i}") for i in range(3)])
    assert "MIPRO" in capsys.readouterr().out
    assert factory() == "GEPA"
