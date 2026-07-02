"""Tests for the `jobcrawl optimize` offline job wiring (§4.11).

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

from jobcrawler.cli_optimize import (
    _build_factory,
    _split_sizes,
    _stable_runs,
    optimize_command,
)
from jobcrawler.generate.generator import CrawlerGenerator
from jobcrawler.state import StateStore


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
    monkeypatch.setenv("JOBCRAWLER_TRAINSET_PATH", str(tmp_path / "trainset.jsonl"))
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", f"sqlite:///{tmp_path}/state.db")

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

    monkeypatch.setattr("jobcrawler.cli_optimize.bootstrap_compile", fake_bootstrap)
    factory = _factory("bootstrap_fewshot")
    assert factory() == "BOOT"
    assert seen["which"] == "bootstrap"


def test_build_factory_gepa_rung_calls_gepa_compile(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    seen: dict[str, str] = {}

    def fake_gepa(baseline: object, **kwargs: object) -> str:
        seen["which"] = "gepa"
        return "GEPA"

    monkeypatch.setattr("jobcrawler.cli_optimize.gepa_compile", fake_gepa)
    factory = _factory("gepa")
    assert factory() == "GEPA"
    assert seen["which"] == "gepa"


def test_build_factory_gepa_rung_requires_reflection_model_eagerly(
    monkeypatch: MonkeyPatch,
) -> None:
    # The reflection model is validated when the factory is *built* (before the
    # expensive minibatch scoring), not deferred into the compile.
    monkeypatch.delenv("JOBCRAWLER_REFLECTION_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="JOBCRAWLER_REFLECTION_MODEL"):
        _factory("gepa")


def test_build_factory_mipro_falls_forward_to_gepa_with_notice(
    monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    # MIPRO is documented-not-built in v1 → use the strongest built rung (GEPA),
    # loudly — never a silent drop to bootstrap.
    monkeypatch.setenv("JOBCRAWLER_REFLECTION_MODEL", "openrouter/strong/reflector")
    monkeypatch.setattr("jobcrawler.cli_optimize.gepa_compile", lambda *a, **k: "GEPA")
    monkeypatch.setattr(
        "jobcrawler.cli_optimize.bootstrap_compile",
        lambda *a, **k: pytest.fail("mipro must not fall through to bootstrap"),
    )
    factory = _factory("mipro", train=[_ex(f"t{i}") for i in range(3)])
    assert "MIPRO" in capsys.readouterr().out
    assert factory() == "GEPA"


# -- autonomous adoption notifies the operator (§4.11, P4b) ------------------


def test_adopt_notifies_after_flipping_the_pointer(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The autonomous --adopt path ("full auto, gated by proof") must push an operator
    # alert right after it swaps the live generator pointer — and the pointer must
    # actually move. Everything upstream of the adopt branch is faked.
    from jobcrawler import cli, cli_optimize, config, generator_pointer, notify

    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_db_path", lambda: ":memory:")
    monkeypatch.setattr(cli_optimize, "load_records", lambda path: [])
    monkeypatch.setattr(
        cli_optimize,
        "optimization_pool",
        lambda *a, **k: [_ex("a"), _ex("b"), _ex("c")],
    )
    monkeypatch.setattr(
        cli_optimize,
        "split_by_site",
        lambda examples, holdout, val: ([_ex("a")], [_ex("b")], [_ex("c")]),
    )
    out = tmp_path / "generator-1.json"
    monkeypatch.setattr(
        cli_optimize,
        "_optimize",
        lambda train, val, holdout: {
            "promoted": True,
            "optimized_program": cast("CrawlerGenerator", "PROG"),
            "baseline": 1.0,
            "optimized": 2.5,
            "margin": 1.5,
        },
    )
    monkeypatch.setattr(cli_optimize, "save_generator", lambda *a, **k: out)

    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        notify,
        "notify",
        lambda event, message, **fields: events.append((event, fields)),
    )

    assert cli_optimize._run_optimize(argparse.Namespace(adopt=True)) == 0
    assert events == [("generator.adopted", {"path": str(out), "margin": 1.5})]
    assert generator_pointer.active_generator_path() == str(out)  # pointer moved
    # ...and put under §4.11 probation so the daemon's regression watcher can revert it.
    marker = generator_pointer.read_adoption()
    assert marker is not None and marker.path == str(out)


def test_no_adopt_does_not_flip_the_pointer_or_notify(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # Without --adopt the manual default only writes + prints a deploy hint: no live
    # pointer flip, and nothing to alert on (the operator chose to stay hands-on).
    from jobcrawler import cli, cli_optimize, config, generator_pointer, notify

    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    monkeypatch.setattr(cli, "_db_path", lambda: ":memory:")
    monkeypatch.setattr(cli_optimize, "load_records", lambda path: [])
    monkeypatch.setattr(
        cli_optimize,
        "optimization_pool",
        lambda *a, **k: [_ex("a"), _ex("b"), _ex("c")],
    )
    monkeypatch.setattr(
        cli_optimize,
        "split_by_site",
        lambda examples, holdout, val: ([_ex("a")], [_ex("b")], [_ex("c")]),
    )
    out = tmp_path / "generator-1.json"
    monkeypatch.setattr(
        cli_optimize,
        "_optimize",
        lambda train, val, holdout: {
            "promoted": True,
            "optimized_program": cast("CrawlerGenerator", "PROG"),
            "baseline": 1.0,
            "optimized": 2.5,
            "margin": 1.5,
        },
    )
    monkeypatch.setattr(cli_optimize, "save_generator", lambda *a, **k: out)
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        notify,
        "notify",
        lambda event, message, **fields: events.append((event, fields)),
    )

    assert cli_optimize._run_optimize(argparse.Namespace(adopt=False)) == 0
    assert events == []
    assert generator_pointer.active_generator_path() is None  # pointer untouched
    assert generator_pointer.read_adoption() is None  # no probation marker either
