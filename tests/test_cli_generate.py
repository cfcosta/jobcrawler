"""Tests for the generate gating logic (§6) and the §4.8/§14.2 delivery path.

The live RLM run (LM + Deno + network) is not unit-tested; the §6 gate set on the
selected winner is. gate_winner runs the real §4.4 sandbox on no-fetch crawler
sources, so it exercises AST → must-observe → completeness → two-run stability. The
delivery helpers (stage vs live + promote) are pure filesystem and tested directly.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jobcrawler import cli_generate, config
from jobcrawler.cli_generate import (
    _deliver,
    gate_winner,
    promote_one,
    regenerate_one,
    rollback_one,
)
from jobcrawler.health import RegenOutcome
from jobcrawler.schedule import Site
from jobcrawler.state import StateStore

# The generation LMs are irrelevant when _run_generation is stubbed; a typed None keeps
# the direct _generate_one calls (which expect dspy.LM) free of type noise.
_NO_LM: Any = None

_GOOD = (
    "from jobcrawler.runtime.contract import Job\n"
    'SITE = "https://example.com"\n'
    "def crawl(ctx):\n"
    "    for i in range(8):\n"
    '        description = f"Role {i}. " + "x" * 500  # distinct per posting\n'
    '        yield Job(url=f"{SITE}/a{i}", title=f"T{i}", company="Acme", '
    'location="Remote", description=description, source="s")\n'
)

_HOSTS = {"example.com"}


def _pred(
    *,
    trajectory: str = "ran validate_crawler and find_feeds before SUBMIT",
    discovery_method: str = "feed",
) -> SimpleNamespace:
    return SimpleNamespace(trajectory=trajectory, discovery_method=discovery_method)


def test_pred_tokens_sums_token_only_usage() -> None:
    # dspy's get_lm_usage() records token counts and NEVER a `cost` key, so the §4.2
    # fan-out cap must read tokens — a token-only usage dict has to come back non-zero.
    # (The old $-cost accounting keyed on "cost" and silently returned 0, making the
    # cap inert.) total_tokens is preferred; otherwise prompt+completion are summed,
    # across every LM a candidate touched (root + sub_lm).
    pred = SimpleNamespace(
        get_lm_usage=lambda: {
            "openrouter/root": {"prompt_tokens": 100, "completion_tokens": 200},
            "chatgpt/sub": {
                "prompt_tokens": 50,
                "completion_tokens": 25,
                "total_tokens": 75,
            },
        }
    )
    assert cli_generate._pred_tokens(pred) == 375.0  # (100 + 200) + 75


def test_pred_tokens_is_zero_without_a_usage_signal() -> None:
    # No usage getter → 0.0; the fan-out then falls back to the structural fanout_n
    # bound and the per-trajectory MAX_SUBCALLS kill, never a fabricated number.
    assert cli_generate._pred_tokens(SimpleNamespace()) == 0.0


def test_good_winner_passes_all_gates() -> None:
    ok, reason = gate_winner(_pred(), _GOOD, _HOSTS)
    assert ok, reason


def test_ast_violation_fails() -> None:
    bad = 'import os\nSITE="https://example.com"\ndef crawl(ctx):\n    yield 1\n'
    ok, reason = gate_winner(_pred(), bad, _HOSTS)
    assert not ok and "AST" in reason


def test_must_observe_unvalidated_fails() -> None:
    ok, reason = gate_winner(_pred(trajectory="submitted immediately"), _GOOD, _HOSTS)
    assert not ok and "must-observe" in reason


def test_scrape_without_consulting_feeds_fails() -> None:
    ok, reason = gate_winner(
        _pred(trajectory="called validate_crawler", discovery_method="scrape"),
        _GOOD,
        _HOSTS,
    )
    assert not ok and "must-observe" in reason


def test_scrape_passes_when_snapshot_already_surfaced_discovery() -> None:
    # The prefetched snapshot always runs feed/sitemap/API discovery and hands it to the
    # model in the corpus, so a candidate that read it and chose scrape HAS consulted —
    # gate 2(a) must not reject it for skipping a redundant in-trajectory find_feeds call.
    # Same pred as test_scrape_without_consulting_feeds_fails, now with the harness credit.
    ok, reason = gate_winner(
        _pred(trajectory="called validate_crawler", discovery_method="scrape"),
        _GOOD,
        _HOSTS,
        discovery_prefetched=True,
    )
    assert ok, reason


def test_snapshot_surfaced_discovery_recognizes_a_prefetched_corpus() -> None:
    from jobcrawler.generate.snapshot import build_corpus

    # A real prefetched corpus always carries feeds/sitemap/apis → discovery was surfaced.
    assert cli_generate._snapshot_surfaced_discovery(build_corpus(homepage="<html/>"))
    # A non-mapping or a bare dict that skipped discovery gets no credit.
    assert not cli_generate._snapshot_surfaced_discovery(None)
    assert not cli_generate._snapshot_surfaced_discovery({"homepage": "x"})


def test_empty_board_signal_reads_the_snapshot_count() -> None:
    from jobcrawler.generate.snapshot import build_corpus

    # ATS candidates found + the probe reported zero openings → the empty-board
    # admission signal (literal 0) that check_completeness keys off.
    assert (
        cli_generate._empty_board_signal(
            build_corpus(
                homepage="<html/>",
                apis=["https://boards-api.greenhouse.io/v1/boards/x/jobs"],
                expected_positions=0,
            )
        )
        == 0
    )
    # A board that lists roles → positive probe count → None (no empty signal), so a
    # broken zero-yield candidate there still fails the enumeration floor.
    assert (
        cli_generate._empty_board_signal(
            build_corpus(homepage="<html/>", apis=["https://x"], expected_positions=12)
        )
        is None
    )
    # No ATS candidate discovered → absence of a signal, not proof of an empty board;
    # don't admit (a custom careers page hiding roles behind JS must not sneak through).
    assert (
        cli_generate._empty_board_signal(
            build_corpus(homepage="<html/>", expected_positions=0)
        )
        is None
    )
    # Non-mapping is safe.
    assert cli_generate._empty_board_signal(None) is None


def test_under_enumeration_fails_when_expected_count_known() -> None:
    # _GOOD yields 8 items; a site with ~40 recent articles → 8 < ceil(0.5 * 40) = 20,
    # so the enumeration floor rejects the feed-sized sample (§6).
    ok, reason = gate_winner(_pred(), _GOOD, _HOSTS, expected_items=40)
    assert not ok and "completeness" in reason


def test_single_posting_passes_min_items_floor() -> None:
    # min_items defaults to 1 (§6): a small careers page with one open role is a
    # legitimate board, so a lone valid posting must clear the completeness gate.
    one = (
        "from jobcrawler.runtime.contract import Job\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        '    yield Job(url=f"{SITE}/a", title="T", company="Acme", '
        'location="Remote", description="x"*500, source="s")\n'
    )
    ok, reason = gate_winner(_pred(), one, _HOSTS)
    assert ok, reason


def test_empty_crawl_fails_completeness() -> None:
    # With an UNKNOWN posting count (no expected_items) a zero yield is still treated
    # as a broken crawler: absent an independent probe proving the board is empty, the
    # gate can't tell "nothing to find" from "found nothing." The explicit
    # expected_items=0 admission is exercised in the next test.
    none = 'SITE = "https://example.com"\ndef crawl(ctx):\n    return iter(())\n'
    ok, reason = gate_winner(_pred(), none, _HOSTS)
    assert not ok and "completeness" in reason


def test_empty_crawl_admitted_when_probe_reports_zero() -> None:
    # When the snapshot's independent probe proves the board advertises zero openings
    # right now (expected_items=0 — a valid ATS meta.total of 0, not an unknown count),
    # a clean zero-yield crawler is the CORRECT crawl and is admitted end-to-end (§6):
    # min_items is skipped, and empty/empty is stable (jaccard 1.0) so the two-run
    # stability gate also passes — instead of regenerating forever against an empty board.
    none = 'SITE = "https://example.com"\ndef crawl(ctx):\n    return iter(())\n'
    ok, reason = gate_winner(_pred(), none, _HOSTS, expected_items=0)
    assert ok, reason


# The Greenhouse shape: the crawl host serves an API whose canonical posting links
# live on a sibling human-facing host the crawler never fetches (absolute_url on
# job-boards.greenhouse.io vs the boards-api crawl host).
_SIBLING_URLS = (
    "from jobcrawler.runtime.contract import Job\n"
    'SITE = "https://example.com"\n'
    "def crawl(ctx):\n"
    "    for i in range(8):\n"
    '        description = f"Role {i}. " + "x" * 500  # distinct per posting\n'
    '        yield Job(url=f"https://boards.example.org/jobs/{i}", title=f"T{i}", '
    'company="Acme", location="Remote", description=description, source="s")\n'
)


def test_sibling_host_urls_fail_without_evidence() -> None:
    # No corpus evidence names the sibling host → the completeness gate rejects the
    # off-allowlist link target (a crawler cannot invent hosts, §8).
    ok, reason = gate_winner(_pred(), _SIBLING_URLS, _HOSTS)
    assert not ok and "completeness" in reason


def test_sibling_host_urls_pass_with_url_evidence() -> None:
    # The same winner once the site's own served corpus names the sibling host
    # (§6 gate 2): the canonical posting link may live there even though the
    # crawler never fetches it — the crawl allowlist itself stays unchanged.
    ok, reason = gate_winner(
        _pred(), _SIBLING_URLS, _HOSTS, url_evidence={"boards.example.org"}
    )
    assert ok, reason


def test_trainset_recorder_widens_url_judgment_not_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The §8 adversarial recorder follows gate 2's evidence rule: its sample-url
    # domain check accepts corpus-evidenced link hosts (a gate-passing Greenhouse
    # winner links job-boards.greenhouse.io while crawling boards-api...), or every
    # delivered ATS-API crawler would be "off-domain" and never enter the trainset.
    # Sandbox *execution* keeps exactly the committed allowlist.
    from collections.abc import Collection, Mapping, Sequence

    from jobcrawler.generate.adversarial import AdversarialResult
    from jobcrawler.generate.scoring import CandidateResult
    from jobcrawler.runtime.ctx import Ctx

    seen: dict[str, set[str]] = {}

    def fake_validate(
        source: str, *, allowed_hosts: Collection[str]
    ) -> CandidateResult:
        seen["execution"] = set(allowed_hosts)
        return CandidateResult(
            item_count=1,
            samples=[{"url": "https://boards.example.org/jobs/1", "title": "T"}],
            schema_errors=[],
            pages_fetched=1,
        )

    def fake_cross_validate(
        samples: Sequence[Mapping[str, object]],
        *,
        live_titles: Mapping[str, str],
        allowed_hosts: Collection[str],
    ) -> AdversarialResult:
        seen["judgment"] = set(allowed_hosts)
        return AdversarialResult(False, 0, 0, 0, "stop before append_record")

    monkeypatch.setattr(cli_generate, "validate_crawler", fake_validate)
    monkeypatch.setattr(cli_generate, "cross_validate", fake_cross_validate)
    monkeypatch.setattr(cli_generate, "_live_titles", lambda ctx, samples: {})
    cli_generate._record_for_trainset(
        Site(slug="acme", url="https://example.com"),
        ctx=Ctx({"example.com"}),  # unused: _live_titles, the only ctx user, is stubbed
        corpus={},
        pred=_pred(),
        source=_SIBLING_URLS,
        hosts={"example.com"},
        url_evidence={"boards.example.org"},
    )
    assert seen["execution"] == {"example.com"}
    assert seen["judgment"] == {"example.com", "boards.example.org"}


# -- §4.8 / §14.2 delivery: stage for review vs write live, and promote ---------

_SRC = "SITE='x'\ndef crawl(ctx):\n    yield None\n"


def test_deliver_stages_to_pending_when_gate_on(tmp_path: Path) -> None:
    outcome = _deliver("acme", _SRC, mode="on", crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.STAGED
    assert (tmp_path / "_pending" / "acme.py").read_text() == _SRC
    assert not (tmp_path / "acme.py").exists()  # live crawler not created


def test_deliver_writes_live_when_gate_off(tmp_path: Path) -> None:
    outcome = _deliver("acme", _SRC, mode="off", crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.PROMOTED
    assert (tmp_path / "acme.py").read_text() == _SRC
    assert not (tmp_path / "_pending").exists()
    assert not (tmp_path / "_history").exists()  # off → no backup


def test_live_delivery_leaves_no_temp_residue(tmp_path: Path) -> None:
    # §4.4: the live crawler is written atomically (temp file + os.replace) because the
    # crawl task now reads it concurrently with generation. After a live write the dir holds
    # exactly the crawler — no leftover ``.tmp`` file that would accumulate on every regen
    # or that a ``*.py`` crawler scan could trip over.
    _deliver("acme", _SRC, mode="off", crawlers_dir=tmp_path)
    assert (tmp_path / "acme.py").read_text() == _SRC
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acme.py"]
    assert cli_generate._committed_slugs(tmp_path) == {"acme"}  # temp never counts


def test_atomic_write_cleans_up_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If the atomic swap fails (a full disk, a permission fault), the temp must not linger:
    # it would accumulate on every failed regen and leave a half-written module in the dir.
    # The call re-raises, but the crawlers dir is left clean and the live crawler untouched.
    def boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(cli_generate.os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        cli_generate._atomic_write_text(tmp_path / "acme.py", _SRC)
    assert (
        list(tmp_path.iterdir()) == []
    )  # temp removed; nothing half-written committed


def test_deliver_creates_crawlers_dir_when_missing(tmp_path: Path) -> None:
    # A relocated JOBCRAWLER_CRAWLERS_DIR (e.g. a fresh /data/crawlers volume) doesn't
    # exist yet on first run; live delivery must create it, not FileNotFoundError (§11).
    target = tmp_path / "crawlers"  # does not exist yet
    outcome = _deliver("acme", _SRC, mode="off", crawlers_dir=target)
    assert outcome is RegenOutcome.PROMOTED
    assert (target / "acme.py").read_text() == _SRC


def test_staged_delivery_never_clobbers_the_live_crawler(tmp_path: Path) -> None:
    # The core safety guarantee: with review_gate on, the daemon keeps running the old
    # (known) crawler until a human promotes — self-heal cannot silently replace it.
    (tmp_path / "acme.py").write_text("LIVE")
    _deliver("acme", _SRC, mode="on", crawlers_dir=tmp_path)
    assert (tmp_path / "acme.py").read_text() == "LIVE"


def test_deliver_auto_backs_up_incumbent_then_promotes(tmp_path: Path) -> None:
    # `auto`: the §4.8 full-auto path — snapshot the live crawler, then overwrite it.
    (tmp_path / "acme.py").write_text("OLD")
    outcome = _deliver("acme", _SRC, mode="auto", crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.PROMOTED
    assert (tmp_path / "acme.py").read_text() == _SRC  # new crawler is live
    backups = list((tmp_path / "_history" / "acme").glob("*.py"))
    assert [b.read_text() for b in backups] == ["OLD"]  # incumbent preserved


def test_deliver_auto_with_no_incumbent_just_promotes(tmp_path: Path) -> None:
    # First-ever delivery: nothing to back up, so auto degenerates to a plain live write.
    outcome = _deliver("acme", _SRC, mode="auto", crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.PROMOTED
    assert (tmp_path / "acme.py").read_text() == _SRC
    assert not (tmp_path / "_history" / "acme").exists()


def test_rollback_restores_prior_crawler(tmp_path: Path) -> None:
    # An auto promotion then a rollback returns the live crawler to the prior version.
    (tmp_path / "acme.py").write_text("OLD")
    _deliver("acme", _SRC, mode="auto", crawlers_dir=tmp_path)
    ok, msg = rollback_one("acme", crawlers_dir=tmp_path)
    assert ok and "acme" in msg
    assert (tmp_path / "acme.py").read_text() == "OLD"  # incumbent restored
    assert list((tmp_path / "_history" / "acme").glob("*.py")) == []  # consumed


def test_rollback_without_a_backup_is_an_error(tmp_path: Path) -> None:
    ok, msg = rollback_one("ghost", crawlers_dir=tmp_path)
    assert not ok and "ghost" in msg
    assert not (tmp_path / "ghost.py").exists()


def test_rollback_walks_back_through_versions(tmp_path: Path) -> None:
    # Two successive auto promotions stack two backups; two rollbacks walk back through
    # them in reverse chronological order (newest restored first).
    (tmp_path / "acme.py").write_text("V0")
    _deliver("acme", "V1", mode="auto", crawlers_dir=tmp_path)  # backs up V0, live=V1
    _deliver("acme", "V2", mode="auto", crawlers_dir=tmp_path)  # backs up V1, live=V2
    assert (tmp_path / "acme.py").read_text() == "V2"

    ok, _ = rollback_one("acme", crawlers_dir=tmp_path)
    assert ok and (tmp_path / "acme.py").read_text() == "V1"  # newest backup first
    ok, _ = rollback_one("acme", crawlers_dir=tmp_path)
    assert ok and (tmp_path / "acme.py").read_text() == "V0"  # then the older one
    ok, _ = rollback_one("acme", crawlers_dir=tmp_path)
    assert not ok  # history exhausted


def test_promote_moves_staged_crawler_live(tmp_path: Path) -> None:
    (tmp_path / "_pending").mkdir()
    (tmp_path / "_pending" / "acme.py").write_text(_SRC)

    ok, msg = promote_one("acme", crawlers_dir=tmp_path)

    assert ok and "acme" in msg
    assert (tmp_path / "acme.py").read_text() == _SRC
    assert not (tmp_path / "_pending" / "acme.py").exists()  # consumed on promote


def test_promote_without_a_staged_crawler_is_an_error(tmp_path: Path) -> None:
    ok, msg = promote_one("ghost", crawlers_dir=tmp_path)
    assert not ok and "ghost" in msg
    assert not (tmp_path / "ghost.py").exists()


def test_regenerate_one_unknown_slug_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_generate, "load_sites", lambda *a, **k: [])
    assert regenerate_one("ghost") is RegenOutcome.FAILED


def _fake_generation(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub the live generation so only the review_gate→mode wiring is exercised."""
    seen: dict[str, object] = {}

    async def fake_generate_one(
        site: Site,
        root_lm: object,
        sub_lm: object,
        *,
        mode: str = "off",
        store: object = None,
    ) -> RegenOutcome:
        seen["mode"] = mode
        seen["store"] = store
        return RegenOutcome.STAGED if mode == "on" else RegenOutcome.PROMOTED

    monkeypatch.setattr(
        cli_generate,
        "load_sites",
        lambda *a, **k: [Site(slug="acme", url="https://acme.com")],
    )
    monkeypatch.setattr(cli_generate, "_build_lms", lambda: (None, None))
    monkeypatch.setattr(cli_generate, "_generate_one", fake_generate_one)
    return seen


def test_regenerate_one_stages_when_review_gate_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_generation(monkeypatch)
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", "on")
    assert regenerate_one("acme") is RegenOutcome.STAGED
    assert seen["mode"] == "on"


def test_regenerate_one_auto_promotes_when_review_gate_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_generation(monkeypatch)
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", "auto")
    assert regenerate_one("acme") is RegenOutcome.PROMOTED
    assert seen["mode"] == "auto"


def test_regenerate_one_goes_live_when_review_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_generation(monkeypatch)
    monkeypatch.setenv("JOBCRAWLER_REVIEW_GATE", "off")
    assert regenerate_one("acme") is RegenOutcome.PROMOTED
    assert seen["mode"] == "off"


def test_regenerate_one_threads_the_store_into_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The self-healer passes the daemon's store so the attempt is recorded (§4.2).
    seen = _fake_generation(monkeypatch)
    store = StateStore(":memory:")
    try:
        regenerate_one("acme", store=store)
        assert seen["store"] is store
    finally:
        store.close()


# -- §4.2 generation-log recording (the capture/persist wrapper) --------------


async def test_generate_one_records_the_attempt_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # _generate_one wraps _run_generation: whatever triple it returns is persisted once,
    # with the captured (here empty) LLM log, so a failure's reason is durable.
    async def fake_run(
        site: Site,
        root_lm: object,
        sub_lm: object,
        *,
        mode: str,
        usage: list[tuple[str, int, int]] | None = None,
    ) -> tuple[RegenOutcome, str, float | None]:
        return RegenOutcome.FAILED, "no candidate produced", None

    monkeypatch.setattr(cli_generate, "_run_generation", fake_run)
    store = StateStore(":memory:")
    try:
        out = await cli_generate._generate_one(
            Site(slug="acme", url="https://acme.com"), _NO_LM, _NO_LM, store=store
        )
        assert out is RegenOutcome.FAILED
        (row,) = store.generation_log("acme")
        assert row["outcome"] == "failed"
        assert row["reason"] == "no candidate produced"
    finally:
        store.close()


async def test_generate_one_records_and_reraises_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unexpected raise (e.g. Deno/network) is still recorded as a failed attempt with the
    # exception as the reason, then re-raised so the healer sees the failure.
    async def boom(
        site: Site,
        root_lm: object,
        sub_lm: object,
        *,
        mode: str,
        usage: list[tuple[str, int, int]] | None = None,
    ) -> tuple[RegenOutcome, str, float | None]:
        raise RuntimeError("deno exploded")

    monkeypatch.setattr(cli_generate, "_run_generation", boom)
    store = StateStore(":memory:")
    try:
        with pytest.raises(RuntimeError, match="deno exploded"):
            await cli_generate._generate_one(
                Site(slug="acme", url="https://acme.com"), _NO_LM, _NO_LM, store=store
            )
        (row,) = store.generation_log("acme")
        assert row["outcome"] == "failed"
        assert "deno exploded" in str(row["reason"])
    finally:
        store.close()


async def test_generate_one_without_a_store_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(
        site: Site,
        root_lm: object,
        sub_lm: object,
        *,
        mode: str,
        usage: list[tuple[str, int, int]] | None = None,
    ) -> tuple[RegenOutcome, str, float | None]:
        return RegenOutcome.PROMOTED, "ok", 0.9

    monkeypatch.setattr(cli_generate, "_run_generation", fake_run)
    # store=None (the operator-less path / tests) must be a clean no-op, not an error.
    out = await cli_generate._generate_one(
        Site(slug="acme", url="https://acme.com"), _NO_LM, _NO_LM
    )
    assert out is RegenOutcome.PROMOTED


async def test_generate_one_ledgers_fanout_usage_per_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every candidate's per-model rows land in the llm_usage ledger, summed per model
    # (root and sub_lm individually) — even for a FAILED attempt, whose tokens were
    # still billed.
    async def fake_run(
        site: Site,
        root_lm: object,
        sub_lm: object,
        *,
        mode: str,
        usage: list[tuple[str, int, int]] | None = None,
    ) -> tuple[RegenOutcome, str, float | None]:
        assert usage is not None
        usage.extend([("root-m", 100, 10), ("root-m", 50, 5), ("sub-m", 7, 3)])
        return RegenOutcome.FAILED, "no candidate produced", None

    monkeypatch.setattr(cli_generate, "_run_generation", fake_run)
    store = StateStore(":memory:")
    try:
        await cli_generate._generate_one(
            Site(slug="acme", url="https://acme.com"), _NO_LM, _NO_LM, store=store
        )
        rows = store.llm_usage_recent(days=1)
        assert [(r["purpose"], r["model"], r["tokens_in"], r["tokens_out"]) for r in rows] == [
            ("generate", "root-m", 150, 15),
            ("generate", "sub-m", 7, 3),
        ]
    finally:
        store.close()


def test_crawlers_dir_default_is_config_driven() -> None:
    # _deliver/promote_one default to the same config-driven dir as the crawl/probe
    # side, so a relocated JOBCRAWLER_CRAWLERS_DIR keeps writer and reader in sync (§11).
    assert cli_generate._CRAWLERS_DIR == Path(config.crawlers_dir())


# -- §8 host admission in the winner gate + evidence eligibility ----------------


def _declaring_extra(source: str) -> str:
    return source.replace(
        'SITE = "https://example.com"\n',
        'SITE = "https://example.com"\nEXTRA_HOSTS = ("api.example.net",)\n',
    )


def test_declared_host_without_evidence_fails_admission() -> None:
    # gate_winner's default is no extra evidence: a winner declaring EXTRA_HOSTS
    # can never pass on declaration alone (§8).
    ok, reason = gate_winner(_pred(), _declaring_extra(_GOOD), _HOSTS)
    assert not ok and "host admission" in reason


def test_declared_evidence_backed_host_passes_the_gate() -> None:
    ok, reason = gate_winner(
        _pred(),
        _declaring_extra(_GOOD),
        _HOSTS,
        extra_evidence=["api.example.net"],
    )
    assert ok, reason


def test_site_drift_fails_admission() -> None:
    # SITE must be the registry entry — the allowlist derives from it (§8).
    drifted = _GOOD.replace('"https://example.com"', '"https://evil.example"')
    ok, reason = gate_winner(_pred(), drifted, _HOSTS)
    assert not ok and "host admission" in reason


def test_committed_hosts_is_registry_plus_declared() -> None:
    src = (
        'SITE = "https://example.com"\n'
        'EXTRA_HOSTS = ("api.example.net",)\n'
        "def crawl(ctx):\n    yield 1\n"
    )
    assert cli_generate._committed_hosts(src, {"example.com"}) == {
        "example.com",
        "api.example.net",
    }


def test_eligible_extra_hosts_drop_robots_forbidden_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The api.getro.com case: a host generation could fetch but production
    # politeness would refuse must never become evidence — it would pass the
    # gate and then die on its first scheduled crawl.
    robots = {
        "https://open.example.net/robots.txt": "User-agent: *\nDisallow:\n",
        "https://closed.example.net/robots.txt": "User-agent: *\nDisallow: /\n",
    }
    monkeypatch.setattr(cli_generate, "_robots_fetch_text", lambda url: robots.get(url))
    assert cli_generate._eligible_extra_hosts(
        ["open.example.net", "closed.example.net"]
    ) == {"open.example.net"}


def test_eligible_extra_hosts_fail_open_on_unreachable_robots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_generate, "_robots_fetch_text", lambda url: None)
    assert cli_generate._eligible_extra_hosts(["a.example.net"]) == {"a.example.net"}


def test_eligible_extra_hosts_bound_the_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []

    def fetcher(url: str) -> str | None:
        probed.append(url)
        return None

    monkeypatch.setattr(cli_generate, "_robots_fetch_text", fetcher)
    many = [f"h{i}.example.net" for i in range(20)]
    assert len(cli_generate._eligible_extra_hosts(many)) == 8
    assert len(probed) == 8


def test_authorized_extra_hosts_reads_the_operator_declaration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §8: a crawler's extra fetch hosts come from the registry entry the operator
    # controls (Site.extra_hosts), robots-permitting — never from page content.
    monkeypatch.setattr(cli_generate, "_robots_fetch_text", lambda url: None)
    site = Site(
        slug="kraken",
        url="https://api.ashbyhq.com/x",
        extra_hosts=["jobs.ashbyhq.com"],
    )
    assert cli_generate._authorized_extra_hosts(site) == {"jobs.ashbyhq.com"}


def test_authorized_extra_hosts_empty_without_a_declaration() -> None:
    # The default: no operator declaration → no extra fetch host. The function takes
    # only the Site, so there is structurally no path for page content to authorize
    # a host — the whole point of the §8 fix.
    site = Site(slug="acme", url="https://acme.com")
    assert cli_generate._authorized_extra_hosts(site) == set()


def test_report_unauthorized_named_hosts_hints_only_uncovered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A host the page names but neither the registry nor the operator authorized is
    # surfaced as an actionable hint; one already covered stays silent.
    cli_generate._report_unauthorized_named_hosts(
        "acme",
        named=["evil.attacker.io", "api.ashbyhq.com", "acme.com"],
        authorized={"api.ashbyhq.com"},
        registry_hosts={"acme.com"},
    )
    out = capsys.readouterr().out
    assert "evil.attacker.io" in out
    assert "api.ashbyhq.com" not in out  # operator-authorized → not flagged
    assert "acme.com" not in out  # the registry host itself → not flagged


def test_committed_slugs_ignores_staging_and_helpers(tmp_path: Path) -> None:
    # Only top-level crawlers/<slug>.py count — _pending/_history staging and any
    # _-prefixed helper are not crawlers, so a staged-only site still reads as missing.
    (tmp_path / "acme.py").write_text("x")
    (tmp_path / "globex.py").write_text("x")
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "_pending").mkdir()
    (tmp_path / "_pending" / "staged.py").write_text("x")
    assert cli_generate._committed_slugs(tmp_path) == {"acme", "globex"}


def test_committed_slugs_missing_dir_is_empty(tmp_path: Path) -> None:
    assert cli_generate._committed_slugs(tmp_path / "nope") == set()


def test_select_missing_returns_enabled_sites_without_a_crawler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # --missing drains the §4.2 backlog: enabled sites with no committed crawler. A site
    # that already has one is skipped; a disabled one never generates.
    sites = [
        Site(slug="has", url="https://has.com"),
        Site(slug="cold", url="https://cold.com"),
        Site(slug="disabled", url="https://d.com", enabled=False),
    ]
    monkeypatch.setattr(cli_generate, "_committed_slugs", lambda: {"has"})
    args = argparse.Namespace(all=False, missing=True, stale=False, slug=None)
    assert [s.slug for s in cli_generate._select(sites, args)] == ["cold"]
