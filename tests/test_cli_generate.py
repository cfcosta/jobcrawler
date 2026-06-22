"""Tests for the generate gating logic (§6) and the §4.8/§14.2 delivery path.

The live RLM run (LM + Deno + network) is not unit-tested; the §6 gate set on the
selected winner is. gate_winner runs the real §4.4 sandbox on no-fetch crawler
sources, so it exercises AST → must-observe → completeness → two-run stability. The
delivery helpers (stage vs live + promote) are pure filesystem and tested directly.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bizup_autocrawler import cli_generate, config
from bizup_autocrawler.cli_generate import (
    _deliver,
    gate_winner,
    promote_one,
    regenerate_one,
)
from bizup_autocrawler.health import RegenOutcome
from bizup_autocrawler.schedule import Site

_GOOD = (
    "from bizup_autocrawler.runtime.contract import Item\n"
    'SITE = "https://example.com"\n'
    "def crawl(ctx):\n"
    "    for i in range(8):\n"
    '        body = f"Article {i}. " + "x" * 500  # distinct per article\n'
    '        yield Item(url=f"{SITE}/a{i}", title=f"T{i}", body=body, source="s")\n'
)

_HOSTS = {"example.com"}


def _pred(
    *,
    trajectory: str = "ran validate_crawler and find_feeds before SUBMIT",
    discovery_method: str = "feed",
) -> SimpleNamespace:
    return SimpleNamespace(trajectory=trajectory, discovery_method=discovery_method)


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


def test_under_enumeration_fails_when_expected_count_known() -> None:
    # _GOOD yields 8 items; a site with ~40 recent articles → 8 < ceil(0.5 * 40) = 20,
    # so the enumeration floor rejects the feed-sized sample (§6).
    ok, reason = gate_winner(_pred(), _GOOD, _HOSTS, expected_items=40)
    assert not ok and "completeness" in reason


def test_too_few_items_fails_completeness() -> None:
    few = (
        "from bizup_autocrawler.runtime.contract import Item\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        '    yield Item(url=f"{SITE}/a", title="T", body="x"*500, source="s")\n'
    )
    ok, reason = gate_winner(_pred(), few, _HOSTS)
    assert not ok and "completeness" in reason


# -- §4.8 / §14.2 delivery: stage for review vs write live, and promote ---------

_SRC = "SITE='x'\ndef crawl(ctx):\n    yield None\n"


def test_deliver_stages_to_pending_when_gated(tmp_path: Path) -> None:
    outcome = _deliver("acme", _SRC, stage=True, crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.STAGED
    assert (tmp_path / "_pending" / "acme.py").read_text() == _SRC
    assert not (tmp_path / "acme.py").exists()  # live crawler not created


def test_deliver_writes_live_when_not_gated(tmp_path: Path) -> None:
    outcome = _deliver("acme", _SRC, stage=False, crawlers_dir=tmp_path)
    assert outcome is RegenOutcome.PROMOTED
    assert (tmp_path / "acme.py").read_text() == _SRC
    assert not (tmp_path / "_pending").exists()


def test_staged_delivery_never_clobbers_the_live_crawler(tmp_path: Path) -> None:
    # The core safety guarantee: with review_gate on, the daemon keeps running the old
    # (known) crawler until a human promotes — self-heal cannot silently replace it.
    (tmp_path / "acme.py").write_text("LIVE")
    _deliver("acme", _SRC, stage=True, crawlers_dir=tmp_path)
    assert (tmp_path / "acme.py").read_text() == "LIVE"


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
    """Stub the live generation so only the review_gate→stage wiring is exercised."""
    seen: dict[str, object] = {}

    async def fake_generate_one(
        site: Site, root_lm: object, sub_lm: object, *, stage: bool = False
    ) -> RegenOutcome:
        seen["stage"] = stage
        return RegenOutcome.STAGED if stage else RegenOutcome.PROMOTED

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
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", "on")
    assert regenerate_one("acme") is RegenOutcome.STAGED
    assert seen["stage"] is True


def test_regenerate_one_goes_live_when_review_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_generation(monkeypatch)
    monkeypatch.setenv("AUTOCRAWLER_REVIEW_GATE", "off")
    assert regenerate_one("acme") is RegenOutcome.PROMOTED
    assert seen["stage"] is False
