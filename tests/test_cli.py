"""Tests for the operator CLI wiring (§10).

The argument parser, list rendering, SITE extraction, db-path parsing, and the
sandbox-backed validate are unit-testable; the live run/generate paths are not
(they need an LM, Deno, and network), so they are only wired here.
"""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from bizup_autocrawler.cli import (
    _db_path,
    _site_url_of,
    build_parser,
    cmd_crawl,
    cmd_generate,
    cmd_list,
    cmd_run,
    cmd_validate,
    render_list,
    validate_one,
)
from bizup_autocrawler.schedule import Site
from bizup_autocrawler.state import StateStore


def test_parser_dispatches_each_command() -> None:
    parser = build_parser()
    assert parser.parse_args(["list"]).func is cmd_list
    assert parser.parse_args(["run"]).func is cmd_run
    assert parser.parse_args(["generate", "--all"]).func is cmd_generate

    validated = parser.parse_args(["validate", "techcrunch"])
    assert validated.func is cmd_validate
    assert validated.slug == "techcrunch"

    crawled = parser.parse_args(["crawl", "--all"])
    assert crawled.func is cmd_crawl
    assert crawled.all is True


def test_site_url_of_reads_the_constant() -> None:
    source = 'SITE = "https://example.com"\ndef crawl(ctx):\n    yield None\n'
    assert _site_url_of(source) == "https://example.com"


def test_db_path_strips_sqlite_scheme(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_STATE_URL", "sqlite:///data/x.db")
    assert _db_path() == "data/x.db"


def test_render_list_shows_script_presence_and_last_items(tmp_path: Path) -> None:
    store = StateStore(":memory:")
    run_id = store.start_run("techcrunch")
    store.finish_run(run_id, items_found=7, new_items=2, errors=0, pages_fetched=3)
    (tmp_path / "techcrunch.py").write_text(
        "SITE='x'\ndef crawl(ctx):\n    yield None\n"
    )
    sites = [
        Site(slug="techcrunch", url="https://t.com"),
        Site(slug="theverge", url="https://v.com", enabled=False),
    ]

    lines = render_list(sites, store, crawlers_dir=tmp_path)

    assert "techcrunch" in lines[0]
    assert "script" in lines[0] and "last_items=" in lines[0] and "7" in lines[0]
    assert "health=ok" in lines[0]  # found items → not stale
    assert "no-script" in lines[1] and " off " in f" {lines[1]} "
    assert "health=new" in lines[1]  # never run
    store.close()


def test_validate_one_runs_the_sandbox(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "from bizup_autocrawler.runtime.contract import Item\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        "    for i in range(3):\n"
        '        yield Item(url=f"{SITE}/a{i}", title=f"T{i}", body="x"*500, source="t")\n'
    )
    result = validate_one("good", crawlers_dir=tmp_path)
    assert result.exception is None
    assert result.item_count == 3
