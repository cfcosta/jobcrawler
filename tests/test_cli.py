"""Tests for the operator CLI wiring (§10).

The argument parser, list rendering, SITE extraction, db-path parsing, and the
sandbox-backed validate are unit-testable; the live run/generate paths are not
(they need an LM, Deno, and network), so they are only wired here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from bizup_autocrawler.ingest import NewsClient

from bizup_autocrawler import config
from bizup_autocrawler.cli import (
    PreflightError,
    _build_healer,
    _configure_logging,
    _db_path,
    _probe,
    _record_site_failure,
    _site_url_of,
    build_parser,
    cmd_crawl,
    cmd_generate,
    cmd_list,
    cmd_promote,
    cmd_run,
    cmd_status,
    cmd_validate,
    crawl_one,
    preflight,
    render_list,
    render_status,
    validate_one,
)
from bizup_autocrawler.governor import RateGovernor
from bizup_autocrawler.health import HealthState, RegenOutcome
from bizup_autocrawler.runtime.runner import CrawlSummary
from bizup_autocrawler.schedule import Site
from bizup_autocrawler.state import StateStore
from bizup_autocrawler.trainset import load_records


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

    promoted = parser.parse_args(["promote", "techcrunch"])
    assert promoted.func is cmd_promote
    assert promoted.slug == "techcrunch"

    assert parser.parse_args(["status"]).func is cmd_status


def test_cmd_promote_returns_zero_on_success(monkeypatch: MonkeyPatch) -> None:
    # cmd_promote wires the slug through cli_generate.promote_one and maps the
    # (ok, message) result to a shell exit code; the move mechanics live in
    # test_cli_generate. A staged crawler found → promoted → exit 0.
    from bizup_autocrawler import cli_generate

    monkeypatch.setattr(
        cli_generate, "promote_one", lambda slug: (True, f"promoted {slug}")
    )
    assert cmd_promote(build_parser().parse_args(["promote", "acme"])) == 0


def test_cmd_promote_returns_one_when_nothing_staged(monkeypatch: MonkeyPatch) -> None:
    # Nothing staged for the slug → non-zero exit so an operator/script sees the miss.
    from bizup_autocrawler import cli_generate

    monkeypatch.setattr(
        cli_generate,
        "promote_one",
        lambda slug: (False, f"no staged crawler for {slug}"),
    )
    assert cmd_promote(build_parser().parse_args(["promote", "ghost"])) == 1


def test_site_url_of_reads_the_constant() -> None:
    source = 'SITE = "https://example.com"\ndef crawl(ctx):\n    yield None\n'
    assert _site_url_of(source) == "https://example.com"


def test_db_path_strips_sqlite_scheme(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCRAWLER_STATE_URL", "sqlite:///data/x.db")
    assert _db_path() == "data/x.db"


def test_configure_logging_honors_the_level_env(monkeypatch: MonkeyPatch) -> None:
    # The daemon entrypoint must route the scheduler's INFO heartbeat to a handler at
    # the configured level — otherwise the run loop's logs are swallowed in production.
    monkeypatch.setenv("AUTOCRAWLER_LOG_LEVEL", "warning")
    pkg = logging.getLogger("bizup_autocrawler")
    original = pkg.level
    try:
        _configure_logging()
        assert pkg.level == logging.WARNING
    finally:
        pkg.setLevel(original)


# -- startup preflight (§4.9/§11 fail-fast) ----------------------------------

_VALID_SITES = "- slug: s0\n  url: https://x.com\n"


def test_preflight_passes_and_returns_the_registry(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    result = preflight(
        sites_path=str(sites),
        db_path=str(tmp_path / "state.db"),
        news_url="http://localhost:8000",
    )
    assert [s.slug for s in result] == ["s0"]


def test_preflight_rejects_a_missing_registry(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="not found"):
        preflight(
            sites_path=str(tmp_path / "nope.yaml"),
            db_path=str(tmp_path / "s.db"),
            news_url="http://x",
        )


def test_preflight_rejects_malformed_sites_yaml(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text("[unterminated")  # not valid YAML
    with pytest.raises(PreflightError, match="YAML"):
        preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            news_url="http://x",
        )


def test_preflight_rejects_an_invalid_site_entry(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text(
        "- slug: has-hyphen\n  url: https://x.com\n"
    )  # slug not import-safe
    with pytest.raises(PreflightError, match="invalid entry"):
        preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            news_url="http://x",
        )


def test_preflight_rejects_a_malformed_news_url(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    with pytest.raises(PreflightError, match="NEWS_API_URL"):
        preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            news_url="not-a-url",
        )


def test_preflight_rejects_an_unwritable_state_dir(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    missing = tmp_path / "nonexistent" / "state.db"  # parent dir does not exist
    with pytest.raises(PreflightError, match="state"):
        preflight(
            sites_path=str(sites),
            db_path=str(missing),
            news_url="http://x",
        )


def test_preflight_warns_on_an_empty_enabled_registry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An all-disabled (or empty) registry is a warning, not fatal — an operator may be
    # staging the list — but the daemon would otherwise idle silently.
    sites = tmp_path / "sites.yaml"
    sites.write_text("- slug: s0\n  url: https://x.com\n  enabled: false\n")
    with caplog.at_level(logging.WARNING, logger="bizup_autocrawler.cli"):
        result = preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            news_url="http://x",
        )
    assert result and not any(s.enabled for s in result)
    assert any("no enabled sites" in r.getMessage() for r in caplog.records)


def test_cmd_run_fails_fast_on_bad_config(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # cmd_run must return nonzero WITHOUT starting the daemon when preflight fails.
    monkeypatch.setenv("AUTOCRAWLER_SITES", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("AUTOCRAWLER_STATE_URL", str(tmp_path / "s.db"))
    assert cmd_run(build_parser().parse_args(["run"])) == 2


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


def test_render_list_surfaces_persisted_generation_failed(tmp_path: Path) -> None:
    # A generation-failed site still looks like a staleness candidate from its run
    # history, so `list` must read the *persisted* state to surface it distinctly
    # (§4.8) rather than re-deriving "stale" and implying it will self-heal.
    store = StateStore(":memory:")
    baseline = store.start_run("techcrunch")
    store.finish_run(baseline, items_found=8, new_items=8, errors=0, pages_fetched=2)
    latest = store.start_run("techcrunch")
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    store.set_health("techcrunch", "generation_failed")

    lines = render_list(
        [Site(slug="techcrunch", url="https://t.com")], store, crawlers_dir=tmp_path
    )

    assert "health=gen-failed" in lines[0]
    store.close()


def test_render_list_surfaces_persisted_pending_review(tmp_path: Path) -> None:
    # A site whose self-heal regen was staged for human promotion (§14.2) reads as a
    # staleness candidate from its run history, so `list` must surface the *persisted*
    # pending-review state distinctly — it is awaiting `autocrawl promote`, not stale.
    store = StateStore(":memory:")
    baseline = store.start_run("techcrunch")
    store.finish_run(baseline, items_found=8, new_items=8, errors=0, pages_fetched=2)
    latest = store.start_run("techcrunch")
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    store.set_health("techcrunch", HealthState.PENDING_REVIEW.value)

    lines = render_list(
        [Site(slug="techcrunch", url="https://t.com")], store, crawlers_dir=tmp_path
    )

    assert "health=pending-review" in lines[0]
    store.close()


# -- status (M4 observability surface) ---------------------------------------


def test_render_status_surfaces_per_site_run_aggregates() -> None:
    # `status` wires metrics.aggregate_runs: each site shows its folded run totals
    # (runs / found / new / errors) so an operator can read the fleet without psql.
    store = StateStore(":memory:")
    r1 = store.start_run("techcrunch")
    store.finish_run(r1, items_found=5, new_items=2, errors=0, pages_fetched=3)
    r2 = store.start_run("techcrunch")
    store.finish_run(r2, items_found=4, new_items=1, errors=1, pages_fetched=2)

    lines = render_status([Site(slug="techcrunch", url="https://t.com")], store)
    site_line = next(line for line in lines if "techcrunch" in line)

    assert "runs=2" in site_line
    assert "found=9" in site_line
    assert "new=3" in site_line
    assert "errors=1" in site_line
    store.close()


def test_render_status_includes_a_fleet_health_distribution() -> None:
    # `status` wires metrics.health_distribution across the registry: persisted states
    # are tallied (and a site with no recorded health reads as healthy-by-default).
    store = StateStore(":memory:")
    for slug in ("a", "b"):
        rid = store.start_run(slug)
        store.finish_run(rid, items_found=1, new_items=1, errors=0, pages_fetched=1)
    store.set_health("a", HealthState.STALE.value)
    # "b" has no persisted health → counted healthy.

    lines = render_status(
        [Site(slug="a", url="https://a.com"), Site(slug="b", url="https://b.com")],
        store,
    )
    fleet_line = next(line for line in lines if "healthy=" in line)

    assert "healthy=1" in fleet_line
    assert "stale=1" in fleet_line
    assert "pending_review=0" in fleet_line
    store.close()


def test_render_status_surfaces_observed_drift_mttr() -> None:
    # `status` wires metrics.drift_mttr over recovery events derived from run history
    # (no transition log exists yet): a live→dark→recovered cycle yields a median.
    store = StateStore(":memory:")
    r1 = store.start_run("techcrunch")
    store.finish_run(r1, items_found=5, new_items=5, errors=0, pages_fetched=2)
    r2 = store.start_run("techcrunch")
    store.finish_run(r2, items_found=0, new_items=0, errors=1, pages_fetched=1)
    r3 = store.start_run("techcrunch")
    store.finish_run(r3, items_found=6, new_items=6, errors=0, pages_fetched=2)

    lines = render_status([Site(slug="techcrunch", url="https://t.com")], store)

    assert any("drift" in line.lower() for line in lines)


def test_render_status_drift_mttr_is_na_without_recovery_events() -> None:
    # No recovered drift in the history → drift MTTR has no data and reads "n/a"
    # rather than fabricating a duration.
    store = StateStore(":memory:")
    rid = store.start_run("techcrunch")
    store.finish_run(rid, items_found=5, new_items=5, errors=0, pages_fetched=2)

    lines = render_status([Site(slug="techcrunch", url="https://t.com")], store)
    drift_line = next(line for line in lines if "drift" in line.lower())

    assert "n/a" in drift_line.lower()
    store.close()


def test_cmd_status_prints_and_returns_zero(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # cmd_status loads the registry + opens the configured store, prints the surface,
    # and exits 0. The rendering is unit-tested above; this pins the wiring.
    sites = tmp_path / "sites.yaml"
    sites.write_text("- slug: techcrunch\n  url: https://t.com\n")
    monkeypatch.setattr(config, "sites_path", lambda: str(sites))
    monkeypatch.setattr(config, "state_url", lambda: "sqlite:///:memory:")

    assert cmd_status(build_parser().parse_args(["status"])) == 0
    out = capsys.readouterr().out
    assert "techcrunch" in out


def test_record_site_failure_writes_a_hard_case(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # A confirmed-failed site is captured as a §4.11 hard case (site identity only —
    # the corpus is re-prefetched fresh at optimize time).
    path = str(tmp_path / "trainset.jsonl")
    monkeypatch.setattr(config, "trainset_path", lambda: path)
    sites = [
        Site(slug="valor", url="https://valor.com", render="static", hints="rss only")
    ]

    _record_site_failure(sites, "valor")

    records = load_records(path)
    assert len(records) == 1
    assert records[0]["kind"] == "hard"
    assert records[0]["slug"] == "valor"
    assert records[0]["site_url"] == "https://valor.com"
    assert records[0]["render"] == "static"


def test_record_site_failure_unknown_slug_is_a_noop(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = str(tmp_path / "trainset.jsonl")
    monkeypatch.setattr(config, "trainset_path", lambda: path)

    _record_site_failure([], "ghost")  # not in the registry

    assert load_records(path) == []


async def test_build_healer_records_hard_case_on_confirmed_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The production §4.8 healer's confirmed-failure hook must feed the §4.11 trainset.
    # Stub the live re-probe/regenerate (they pull an LM + the live crawler); only the
    # wiring of on_confirmed_failure -> _record_site_failure is under test.
    path = str(tmp_path / "trainset.jsonl")
    monkeypatch.setattr(config, "trainset_path", lambda: path)
    store = StateStore(":memory:")
    baseline = store.start_run("valor")
    store.finish_run(baseline, items_found=5, new_items=5, errors=0, pages_fetched=1)
    latest = store.start_run("valor")
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    sites = [Site(slug="valor", url="https://valor.com")]

    healer = _build_healer(store, sites)

    async def _zero(slug: str) -> int:
        return 0  # re-probe confirms a real failure, not a transient outage

    async def _failed(slug: str) -> RegenOutcome:
        return RegenOutcome.FAILED

    healer._reprobe = _zero
    healer._regenerate = _failed

    state = await healer.assess("valor")

    assert state is HealthState.GENERATION_FAILED
    assert [r["kind"] for r in load_records(path)] == ["hard"]
    store.close()


def test_cmd_crawl_shares_one_governor_across_sites(monkeypatch: MonkeyPatch) -> None:
    # §4.7: every crawl in a single invocation must share ONE ingest governor so the
    # global news-API pacing holds. A per-crawl governor (the prior crawl_site
    # fallback) would let N concurrent sites each pace independently → N× the cap.
    seen: list[RateGovernor | None] = []

    def fake_crawl_one(
        site: Site,
        *,
        store: StateStore,
        news: object,
        governor: RateGovernor | None = None,
    ) -> CrawlSummary:
        seen.append(governor)
        return CrawlSummary(
            site.slug, items_found=0, new_items=0, errors=0, pages_fetched=0
        )

    monkeypatch.setenv("AUTOCRAWLER_STATE_URL", ":memory:")
    monkeypatch.setattr(
        "bizup_autocrawler.cli.load_sites",
        lambda _path: [
            Site(slug="a", url="https://a.com"),
            Site(slug="b", url="https://b.com"),
        ],
    )
    monkeypatch.setattr("bizup_autocrawler.cli.crawl_one", fake_crawl_one)

    assert cmd_crawl(build_parser().parse_args(["crawl", "--all"])) == 0
    assert len(seen) == 2
    assert seen[0] is not None
    assert seen[0] is seen[1]  # the SAME instance threaded through, not one per site


def test_crawl_one_isolates_a_hanging_committed_crawler(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The #52 guarantee: a committed crawler that loops without fetching never trips
    # the page budget, so run in-process it would wedge the daemon (and the whole
    # fleet) forever. crawl_one must execute it in the bounded §4.4 subprocess — a
    # hang comes back as a finalized failed run (0 items, >=1 error), never a hung
    # call, and nothing is ingested.
    (tmp_path / "hang.py").write_text(
        'SITE = "https://h.com"\ndef crawl(ctx):\n    while True:\n        pass\n'
    )
    monkeypatch.setenv("AUTOCRAWLER_CRAWL_TIMEOUT", "1")
    monkeypatch.setenv("AUTOCRAWLER_CRAWL_CPU_SECONDS", "1")
    store = StateStore(":memory:")

    class _News:
        def post_item(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("a bounded-out crawler must not ingest")

    summary = crawl_one(
        Site(slug="hang", url="https://h.com", name="Hang"),
        store=store,
        news=cast(NewsClient, _News()),
        crawlers_dir=tmp_path,
    )

    assert summary.items_found == 0
    assert summary.errors >= 1
    history = store.runs_for("hang")
    assert len(history) == 1 and history[0].finished is not None
    store.close()


def test_probe_counts_complete_items_through_isolation(tmp_path: Path) -> None:
    # _probe is the self-healer's confirm-a-real-failure re-run; it must run the
    # committed script through the same bounded subprocess and count only complete
    # items (no ingest, no state). A healthy script reports its complete-item count.
    (tmp_path / "good.py").write_text(
        "from bizup_autocrawler.runtime.contract import Item\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        "    for i in range(3):\n"
        '        yield Item(url=f"{SITE}/a{i}", title=f"T{i}", body="x"*500, source="t")\n'
    )
    assert _probe("good", crawlers_dir=tmp_path) == 3


def test_probe_is_zero_when_the_script_is_missing(tmp_path: Path) -> None:
    # A site whose committed script is missing probes to 0 (a confirmed failure →
    # regenerate), never an exception that aborts the self-heal loop.
    assert _probe("ghost", crawlers_dir=tmp_path) == 0


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
