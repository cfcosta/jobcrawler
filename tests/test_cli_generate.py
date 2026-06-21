"""Tests for the generate gating logic (§6).

The live RLM run (LM + Deno + network) is not unit-tested; the §6 gate set on the
selected winner is. gate_winner runs the real §4.4 sandbox on no-fetch crawler
sources, so it exercises AST → must-observe → completeness → two-run stability.
"""

from __future__ import annotations

from types import SimpleNamespace

from bizup_autocrawler.cli_generate import gate_winner

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


def test_too_few_items_fails_completeness() -> None:
    few = (
        "from bizup_autocrawler.runtime.contract import Item\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        '    yield Item(url=f"{SITE}/a", title="T", body="x"*500, source="s")\n'
    )
    ok, reason = gate_winner(_pred(), few, _HOSTS)
    assert not ok and "completeness" in reason
