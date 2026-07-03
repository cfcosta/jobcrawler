"""Tests for the operator CLI wiring (§10).

The argument parser, list/status/positions/matches rendering, SITE extraction,
db-path parsing, the profile view, the rematch cache-drain, and the sandbox-backed
validate/probe are unit-testable; the live run/generate paths are not (they need an
LM, Deno, and network), so they are only wired here. The §6 matcher is faked or its
constructor monkeypatched — no test bills a token.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from jobcrawler import config, generator_pointer
from jobcrawler.cli import (
    PreflightError,
    _build_healer,
    _configure_logging,
    _db_path,
    _humanize_secs,
    _probe,
    _record_site_failure,
    _run_rematch,
    build_parser,
    cmd_crawl,
    cmd_generate,
    cmd_list,
    cmd_matches,
    cmd_positions,
    cmd_profile,
    cmd_promote,
    cmd_rematch,
    cmd_rollback,
    cmd_rollback_generator,
    cmd_run,
    cmd_status,
    cmd_validate,
    crawl_one,
    preflight,
    render_list,
    render_matches,
    render_positions,
    render_status,
    validate_one,
)
from jobcrawler.health import HealthState, RegenOutcome
from jobcrawler.match import PROMPT_VERSION
from jobcrawler.profile import load_profile
from jobcrawler.runtime.contract import allowed_hosts_of
from jobcrawler.runtime.runner import CrawlSummary
from jobcrawler.schedule import Site
from jobcrawler.state import StateStore
from jobcrawler.trainset import load_records

# A minimal but *valid* profile (frontmatter enums from the §4.6 vocabulary + a body),
# and a malformed one (an unknown discipline) that load_profile rejects loudly.
_VALID_PROFILE = "---\ndisciplines:\n  - backend\nworkplace:\n  - remote\n---\n\nSenior backend engineer.\n"
_MALFORMED_PROFILE = "---\ndisciplines:\n  - not_a_real_discipline\n---\n\nbody\n"


def _seed_position(
    store: StateStore,
    *,
    site: str = "acme",
    url: str,
    title: str,
    company: str = "Acme",
    location: str = "Remote",
    workplace: str | None = "remote",
    content_hash: str,
    score: int | None = None,
    verdict: str = "strong",
    reasons: list[str] | None = None,
    pending: bool = False,
) -> None:
    """Upsert one open position and optionally attach a verdict / pending flag (§4.7)."""
    fields: dict[str, object] = {
        "title": title,
        "company": company,
        "location": location,
    }
    if workplace is not None:
        fields["workplace"] = workplace
    store.upsert_position(site, url, fields=fields, content_hash=content_hash)
    if score is not None:
        store.set_match(
            site, url, score=score, verdict=verdict, reasons=json.dumps(reasons or [])
        )
    if pending:
        store.set_match_pending(site, url)


def test_parser_dispatches_each_command() -> None:
    parser = build_parser()
    assert parser.parse_args(["list"]).func is cmd_list
    assert parser.parse_args(["run"]).func is cmd_run
    assert parser.parse_args(["generate", "--all"]).func is cmd_generate

    validated = parser.parse_args(["validate", "greenhouse"])
    assert validated.func is cmd_validate
    assert validated.slug == "greenhouse"

    crawled = parser.parse_args(["crawl", "--all"])
    assert crawled.func is cmd_crawl
    assert crawled.all is True

    promoted = parser.parse_args(["promote", "greenhouse"])
    assert promoted.func is cmd_promote
    assert promoted.slug == "greenhouse"

    rolled_back = parser.parse_args(["rollback", "greenhouse"])
    assert rolled_back.func is cmd_rollback
    assert rolled_back.slug == "greenhouse"

    assert parser.parse_args(["rollback-generator"]).func is cmd_rollback_generator

    # --adopt is opt-in: off by default (cautious manual optimize), on when asked
    # (the path the autonomous reopt dispatch takes to flip the live pointer, §4.11).
    assert parser.parse_args(["optimize"]).adopt is False
    assert parser.parse_args(["optimize", "--adopt"]).adopt is True

    assert parser.parse_args(["status"]).func is cmd_status


def test_parser_dispatches_the_job_tracking_commands() -> None:
    # The §4.7/§6 job surfaces: positions/matches/rematch/profile.
    parser = build_parser()
    assert parser.parse_args(["positions"]).func is cmd_positions
    pos = parser.parse_args(
        [
            "positions",
            "--site",
            "acme",
            "--status",
            "all",
            "--min-score",
            "80",
            "--limit",
            "10",
        ]
    )
    assert pos.site == "acme"
    assert pos.status == "all"
    assert pos.min_score == 80
    assert pos.limit == 10
    assert parser.parse_args(["positions"]).status == "open"  # default

    assert parser.parse_args(["matches"]).func is cmd_matches
    assert parser.parse_args(["matches", "--min", "50"]).min == 50
    assert parser.parse_args(["matches"]).min is None  # defaults to config at runtime

    assert parser.parse_args(["rematch"]).func is cmd_rematch
    assert parser.parse_args(["profile"]).func is cmd_profile


def test_cmd_promote_returns_zero_on_success(monkeypatch: MonkeyPatch) -> None:
    # cmd_promote wires the slug through cli_generate.promote_one and maps the
    # (ok, message) result to a shell exit code; the move mechanics live in
    # test_cli_generate. A staged crawler found → promoted → exit 0.
    from jobcrawler import cli_generate

    monkeypatch.setattr(
        cli_generate, "promote_one", lambda slug: (True, f"promoted {slug}")
    )
    assert cmd_promote(build_parser().parse_args(["promote", "acme"])) == 0


def test_cmd_promote_returns_one_when_nothing_staged(monkeypatch: MonkeyPatch) -> None:
    # Nothing staged for the slug → non-zero exit so an operator/script sees the miss.
    from jobcrawler import cli_generate

    monkeypatch.setattr(
        cli_generate,
        "promote_one",
        lambda slug: (False, f"no staged crawler for {slug}"),
    )
    assert cmd_promote(build_parser().parse_args(["promote", "ghost"])) == 1


def test_cmd_rollback_returns_zero_on_success(monkeypatch: MonkeyPatch) -> None:
    # cmd_rollback wires the slug through cli_generate.rollback_one and maps the
    # (ok, message) result to a shell exit code; the restore mechanics live in
    # test_cli_generate. A backup found → restored → exit 0.
    from jobcrawler import cli_generate

    monkeypatch.setattr(
        cli_generate, "rollback_one", lambda slug: (True, f"rolled back {slug}")
    )
    assert cmd_rollback(build_parser().parse_args(["rollback", "acme"])) == 0


def test_cmd_rollback_returns_one_when_no_backup(monkeypatch: MonkeyPatch) -> None:
    # No backup snapshot for the slug → non-zero exit so an operator/script sees the miss.
    from jobcrawler import cli_generate

    monkeypatch.setattr(
        cli_generate,
        "rollback_one",
        lambda slug: (False, f"no backup to roll back to for {slug}"),
    )
    assert cmd_rollback(build_parser().parse_args(["rollback", "ghost"])) == 1


def test_cmd_rollback_generator_returns_zero_on_success(
    monkeypatch: MonkeyPatch,
) -> None:
    # cmd_rollback_generator undoes the last generator adoption via the pointer's
    # rollback_active and maps the (ok, restored) result to an exit code. A prior
    # adoption found → reverted → exit 0 (the unwind mechanics live in
    # test_generator_pointer).
    from jobcrawler import generator_pointer

    monkeypatch.setattr(
        generator_pointer,
        "rollback_active",
        lambda: (True, "generators/generator-1.json"),
    )
    assert (
        cmd_rollback_generator(build_parser().parse_args(["rollback-generator"])) == 0
    )


def test_cmd_rollback_generator_returns_one_when_nothing_to_roll_back(
    monkeypatch: MonkeyPatch,
) -> None:
    # No prior adoption (already at baseline) → non-zero exit so an operator/script
    # sees there was nothing to undo.
    from jobcrawler import generator_pointer

    monkeypatch.setattr(generator_pointer, "rollback_active", lambda: (False, None))
    assert (
        cmd_rollback_generator(build_parser().parse_args(["rollback-generator"])) == 1
    )


def test_cmd_rollback_generator_clears_the_probation_marker(
    monkeypatch: MonkeyPatch,
) -> None:
    # A successful manual rollback must also consume the adoption-under-probation
    # marker, or the §4.11 auto-rollback watcher would keep judging (and possibly
    # re-rolling) the generator the operator just reverted.
    from jobcrawler import generator_pointer

    cleared = False

    def _clear() -> None:
        nonlocal cleared
        cleared = True

    monkeypatch.setattr(
        generator_pointer,
        "rollback_active",
        lambda: (True, "generators/generator-1.json"),
    )
    monkeypatch.setattr(generator_pointer, "clear_adoption", _clear)

    assert (
        cmd_rollback_generator(build_parser().parse_args(["rollback-generator"])) == 0
    )
    assert cleared


def test_cmd_rollback_generator_leaves_marker_when_nothing_to_roll_back(
    monkeypatch: MonkeyPatch,
) -> None:
    # Nothing to undo → don't touch the marker (there is no adoption to de-probate).
    from jobcrawler import generator_pointer

    cleared = False

    def _clear() -> None:
        nonlocal cleared
        cleared = True

    monkeypatch.setattr(generator_pointer, "rollback_active", lambda: (False, None))
    monkeypatch.setattr(generator_pointer, "clear_adoption", _clear)

    assert (
        cmd_rollback_generator(build_parser().parse_args(["rollback-generator"])) == 1
    )
    assert not cleared


def test_cli_allowlist_is_derived_from_committed_source() -> None:
    # The crawl/validate/probe surfaces all derive the allowlist from the
    # committed source via contract.allowed_hosts_of (SITE + EXTRA_HOSTS, §8).
    source = (
        'SITE = "https://example.com"\n'
        'EXTRA_HOSTS = ("api.example.net",)\n'
        "def crawl(ctx):\n    yield None\n"
    )
    assert allowed_hosts_of(source) == {"example.com", "api.example.net"}


def test_db_path_strips_sqlite_scheme(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", "sqlite:///data/x.db")
    assert _db_path() == "data/x.db"


def test_configure_logging_honors_the_level_env(monkeypatch: MonkeyPatch) -> None:
    # The daemon entrypoint must route the scheduler's INFO heartbeat to a handler at
    # the configured level — otherwise the run loop's logs are swallowed in production.
    monkeypatch.setenv("JOBCRAWLER_LOG_LEVEL", "warning")
    pkg = logging.getLogger("jobcrawler")
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
    profile = tmp_path / "PROFILE.md"
    profile.write_text(_VALID_PROFILE)
    result = preflight(
        sites_path=str(sites),
        db_path=str(tmp_path / "state.db"),
        profile_path=str(profile),
    )
    assert [s.slug for s in result] == ["s0"]


def test_preflight_rejects_a_missing_registry(tmp_path: Path) -> None:
    with pytest.raises(PreflightError, match="not found"):
        preflight(
            sites_path=str(tmp_path / "nope.yaml"),
            db_path=str(tmp_path / "s.db"),
            profile_path=str(tmp_path / "PROFILE.md"),
        )


def test_preflight_rejects_malformed_sites_yaml(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text("[unterminated")  # not valid YAML
    with pytest.raises(PreflightError, match="YAML"):
        preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            profile_path=str(tmp_path / "PROFILE.md"),
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
            profile_path=str(tmp_path / "PROFILE.md"),
        )


def test_preflight_warns_on_a_missing_profile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A MISSING PROFILE.md is a valid crawl-only mode (positions still tracked, just not
    # match-scored), so preflight only warns and still returns the registry — its absence
    # must never block a crawl.
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    with caplog.at_level(logging.WARNING, logger="jobcrawler.cli"):
        result = preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            profile_path=str(tmp_path / "PROFILE.md"),  # does not exist
        )
    assert [s.slug for s in result] == ["s0"]
    assert any("no profile" in r.getMessage() for r in caplog.records)


def test_preflight_rejects_a_malformed_profile(tmp_path: Path) -> None:
    # A MALFORMED PROFILE.md (bad prefs enum) is a loud fail-fast: the operator authored
    # broken preferences and expects matching, so exit 2 rather than silently mismatch.
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    profile = tmp_path / "PROFILE.md"
    profile.write_text(_MALFORMED_PROFILE)
    with pytest.raises(PreflightError, match="malformed"):
        preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            profile_path=str(profile),
        )


def test_preflight_rejects_an_unwritable_state_dir(tmp_path: Path) -> None:
    sites = tmp_path / "sites.yaml"
    sites.write_text(_VALID_SITES)
    missing = tmp_path / "nonexistent" / "state.db"  # parent dir does not exist
    with pytest.raises(PreflightError, match="state"):
        preflight(
            sites_path=str(sites),
            db_path=str(missing),
            profile_path=str(tmp_path / "PROFILE.md"),
        )


def test_preflight_warns_on_an_empty_enabled_registry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An all-disabled (or empty) registry is a warning, not fatal — an operator may be
    # staging the list — but the daemon would otherwise idle silently.
    sites = tmp_path / "sites.yaml"
    sites.write_text("- slug: s0\n  url: https://x.com\n  enabled: false\n")
    with caplog.at_level(logging.WARNING, logger="jobcrawler.cli"):
        result = preflight(
            sites_path=str(sites),
            db_path=str(tmp_path / "s.db"),
            profile_path=str(tmp_path / "PROFILE.md"),
        )
    assert result and not any(s.enabled for s in result)
    assert any("no enabled sites" in r.getMessage() for r in caplog.records)


def test_cmd_run_fails_fast_on_bad_config(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # cmd_run must return nonzero WITHOUT starting the daemon when preflight fails.
    monkeypatch.setenv("JOBCRAWLER_SITES", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(tmp_path / "s.db"))
    assert cmd_run(build_parser().parse_args(["run"])) == 2


def test_render_list_shows_script_presence_and_last_positions(tmp_path: Path) -> None:
    store = StateStore(":memory:")
    run_id = store.start_run("greenhouse")
    store.finish_run(run_id, items_found=7, new_items=2, errors=0, pages_fetched=3)
    (tmp_path / "greenhouse.py").write_text(
        "SITE='x'\ndef crawl(ctx):\n    yield None\n"
    )
    sites = [
        Site(slug="greenhouse", url="https://g.com"),
        Site(slug="lever", url="https://l.com", enabled=False),
    ]

    lines = render_list(sites, store, crawlers_dir=tmp_path)

    assert "greenhouse" in lines[0]
    assert "script" in lines[0] and "positions=" in lines[0] and "7" in lines[0]
    assert "health=ok" in lines[0]  # found positions → not stale
    assert "no-script" in lines[1] and " off " in f" {lines[1]} "
    assert "health=new" in lines[1]  # never run
    store.close()


def test_render_list_surfaces_persisted_generation_failed(tmp_path: Path) -> None:
    # A generation-failed site still looks like a staleness candidate from its run
    # history, so `list` must read the *persisted* state to surface it distinctly
    # (§4.8) rather than re-deriving "stale" and implying it will self-heal.
    store = StateStore(":memory:")
    baseline = store.start_run("greenhouse")
    store.finish_run(baseline, items_found=8, new_items=8, errors=0, pages_fetched=2)
    latest = store.start_run("greenhouse")
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    store.set_health("greenhouse", "generation_failed")

    lines = render_list(
        [Site(slug="greenhouse", url="https://g.com")], store, crawlers_dir=tmp_path
    )

    assert "health=gen-failed" in lines[0]
    store.close()


def test_render_list_surfaces_persisted_pending_review(tmp_path: Path) -> None:
    # A site whose self-heal regen was staged for human promotion (§14.2) reads as a
    # staleness candidate from its run history, so `list` must surface the *persisted*
    # pending-review state distinctly — it is awaiting `jobcrawl promote`, not stale.
    store = StateStore(":memory:")
    baseline = store.start_run("greenhouse")
    store.finish_run(baseline, items_found=8, new_items=8, errors=0, pages_fetched=2)
    latest = store.start_run("greenhouse")
    store.finish_run(latest, items_found=0, new_items=0, errors=1, pages_fetched=1)
    store.set_health("greenhouse", HealthState.PENDING_REVIEW.value)

    lines = render_list(
        [Site(slug="greenhouse", url="https://g.com")], store, crawlers_dir=tmp_path
    )

    assert "health=pending-review" in lines[0]
    store.close()


# -- list: §4.9 learned-cadence column (P4d) ---------------------------------


def test_render_list_shows_the_learned_adaptive_interval(tmp_path: Path) -> None:
    # An adaptive site whose controller has learned an interval shows it humanized, so
    # an operator can see how often the source is actually being polled.
    store = StateStore(":memory:")
    store.set_interval("greenhouse", 7200.0)  # 2h

    lines = render_list(
        [Site(slug="greenhouse", url="https://g.com", adaptive=True)],
        store,
        crawlers_dir=tmp_path,
    )

    assert "every=2h" in lines[0]
    store.close()


def test_render_list_shows_adaptive_before_an_interval_is_learned(
    tmp_path: Path,
) -> None:
    # An adaptive source with no learned interval yet reads `adaptive` (it seeds from a
    # default until the first crawl), not a fabricated number.
    store = StateStore(":memory:")

    lines = render_list(
        [Site(slug="greenhouse", url="https://g.com", adaptive=True)],
        store,
        crawlers_dir=tmp_path,
    )

    assert "every=adaptive" in lines[0]
    store.close()


def test_render_list_shows_fixed_for_a_non_adaptive_site(tmp_path: Path) -> None:
    store = StateStore(":memory:")

    lines = render_list(
        [Site(slug="greenhouse", url="https://g.com")], store, crawlers_dir=tmp_path
    )

    assert "every=fixed" in lines[0]
    store.close()


def test_humanize_secs_uses_compact_units() -> None:
    assert _humanize_secs(45) == "45s"
    assert _humanize_secs(900) == "15m"
    assert _humanize_secs(7200) == "2h"
    assert _humanize_secs(5400) == "1.5h"  # arbitrary controller floats round cleanly
    assert _humanize_secs(86400) == "1d"


# -- status (M4 observability surface) ---------------------------------------


def test_render_status_surfaces_per_site_run_aggregates() -> None:
    # `status` wires metrics.aggregate_runs: each site shows its folded run totals
    # (runs / found / new / errors) so an operator can read the fleet without psql.
    store = StateStore(":memory:")
    r1 = store.start_run("greenhouse")
    store.finish_run(r1, items_found=5, new_items=2, errors=0, pages_fetched=3)
    r2 = store.start_run("greenhouse")
    store.finish_run(r2, items_found=4, new_items=1, errors=1, pages_fetched=2)

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)
    site_line = next(line for line in lines if "greenhouse" in line)

    assert "runs=2" in site_line
    assert "found=9" in site_line
    assert "new=3" in site_line
    assert "errors=1" in site_line
    # errors/runs is a mean-errors-PER-RUN (can exceed 1.0), not a 0-1 rate; the
    # label must say so rather than masquerade as a fraction an operator misreads.
    assert "errs/run=0.50" in site_line
    assert "err_rate=" not in site_line
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
    r1 = store.start_run("greenhouse")
    store.finish_run(r1, items_found=5, new_items=5, errors=0, pages_fetched=2)
    r2 = store.start_run("greenhouse")
    store.finish_run(r2, items_found=0, new_items=0, errors=1, pages_fetched=1)
    r3 = store.start_run("greenhouse")
    store.finish_run(r3, items_found=6, new_items=6, errors=0, pages_fetched=2)

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)

    assert any("drift" in line.lower() for line in lines)


def test_render_status_drift_mttr_is_na_without_recovery_events() -> None:
    # No recovered drift in the history → drift MTTR has no data and reads "n/a"
    # rather than fabricating a duration.
    store = StateStore(":memory:")
    rid = store.start_run("greenhouse")
    store.finish_run(rid, items_found=5, new_items=5, errors=0, pages_fetched=2)

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)
    drift_line = next(line for line in lines if "drift" in line.lower())

    assert "n/a" in drift_line.lower()


def test_render_status_prefers_measured_mttr_from_transition_log() -> None:
    # When the durable health-transition log has data, `status` reports the *measured*
    # §4.8 MTTR (dark onset → return to healthy), dropping the reconstructed "(observed)"
    # qualifier it uses when only run history is available.
    times = iter(
        [
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),  # None -> healthy
            datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),  # healthy -> stale (dark)
            datetime(
                2026, 1, 1, 3, 0, tzinfo=timezone.utc
            ),  # stale -> healthy (recovered)
        ]
    )
    store = StateStore(":memory:", clock=lambda: next(times))
    store.set_health("greenhouse", HealthState.HEALTHY.value)
    store.set_health("greenhouse", HealthState.STALE.value)
    store.set_health("greenhouse", HealthState.HEALTHY.value)

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)
    drift_line = next(line for line in lines if "drift" in line.lower())

    assert "(observed)" not in drift_line  # measured, not reconstructed
    assert "2:00:00" in drift_line  # 01:00 dark -> 03:00 recovered == 2h
    store.close()


def test_render_status_surfaces_mean_body_quality() -> None:
    # The per-site line surfaces mean body_quality (partial-drift signal) from run
    # signals; a site whose runs carry no signal reads body_q=n/a.
    store = StateStore(":memory:")
    r1 = store.start_run("greenhouse")
    store.finish_run(
        r1,
        items_found=4,
        new_items=4,
        errors=0,
        pages_fetched=2,
        signals={"body_quality": 0.5},
    )
    r2 = store.start_run("greenhouse")
    store.finish_run(
        r2,
        items_found=4,
        new_items=0,
        errors=0,
        pages_fetched=2,
        signals={"body_quality": 1.0},
    )

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)
    site_line = next(line for line in lines if "greenhouse" in line)

    assert "body_q=0.75" in site_line
    store.close()


# -- status: §4.7/§6 job-tracking block --------------------------------------


def test_render_status_surfaces_position_lifecycle_and_top_match(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The status footer surfaces the §4.7 lifecycle tallies, today's §6 match spend
    # against the daily cap, and the single strongest open match as a teaser.
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    store = StateStore(":memory:")
    _seed_position(store, url="https://acme.com/a", title="A", content_hash="hA")
    _seed_position(
        store,
        url="https://acme.com/b",
        title="Staff Engineer",
        content_hash="hB",
        score=90,
        reasons=["great fit"],
    )
    _seed_position(store, url="https://acme.com/c", title="C", content_hash="hC")
    store.mark_closed("acme", "https://acme.com/c", reason="expired")

    lines = render_status([], store)
    pos_line = next(line for line in lines if line.startswith("positions:"))
    spend_line = next(line for line in lines if line.startswith("match spend:"))
    top_line = next(line for line in lines if line.startswith("top match:"))

    assert "open=2" in pos_line and "closed=1" in pos_line and "matched=1" in pos_line
    assert "0/" in spend_line and "today" in spend_line
    assert "[90]" in top_line and "Staff Engineer @ Acme" in top_line
    store.close()


def test_render_status_top_match_reads_none_when_nothing_scored(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    store = StateStore(":memory:")
    _seed_position(store, url="https://acme.com/a", title="A", content_hash="hA")

    top_line = next(
        line for line in render_status([], store) if line.startswith("top match:")
    )
    assert top_line == "top match: none"
    store.close()


# -- status: §4.11 self-improvement state + drift churn (P4d) -----------------


def test_render_status_shows_per_site_transition_count() -> None:
    # Each per-site line carries how many health-log rows the site has accrued — its
    # drift churn — so a repeatedly-flapping site is visible at a glance.
    times = iter(
        [
            datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),  # None -> healthy
            datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),  # healthy -> stale
            datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc),  # stale -> healthy
        ]
    )
    store = StateStore(":memory:", clock=lambda: next(times))
    store.set_health("greenhouse", HealthState.HEALTHY.value)
    store.set_health("greenhouse", HealthState.STALE.value)
    store.set_health("greenhouse", HealthState.HEALTHY.value)

    lines = render_status([Site(slug="greenhouse", url="https://g.com")], store)
    site_line = next(line for line in lines if "greenhouse" in line)

    assert "transitions=3" in site_line
    store.close()


def test_render_status_generator_line_is_baseline_without_a_pointer(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    store = StateStore(":memory:")

    gen_line = next(
        line for line in render_status([], store) if line.startswith("generator:")
    )

    assert gen_line == "generator: baseline"
    store.close()


def test_render_status_generator_line_shows_version_and_heldout(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # With a generator adopted, status names the live versioned artifact and the
    # held-out margin it cleared — the §4.11 provenance an operator can't otherwise see.
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    (tmp_path / "generator-2.json").write_text("{}")
    (tmp_path / "generator-2.meta.json").write_text('{"heldout_score": 3.5}')
    generator_pointer.set_active(str(tmp_path / "generator-2.json"))
    store = StateStore(":memory:")

    gen_line = next(
        line for line in render_status([], store) if line.startswith("generator:")
    )

    assert "generator-2.json" in gen_line
    assert "heldout=3.50" in gen_line
    assert "probation" not in gen_line  # no marker → not under probation
    store.close()


def test_render_status_generator_line_flags_probation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # While the P4c watcher still holds an adoption under probation, status flags it so
    # an operator knows the daemon may yet auto-roll it back on its own.
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    (tmp_path / "generator-2.json").write_text("{}")
    generator_pointer.set_active(str(tmp_path / "generator-2.json"))
    generator_pointer.record_adoption(
        str(tmp_path / "generator-2.json"),
        at=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
    )
    store = StateStore(":memory:")

    gen_line = next(
        line for line in render_status([], store) if line.startswith("generator:")
    )

    assert "under probation" in gen_line
    assert "2026-01-10T12:00:00" in gen_line
    store.close()


def test_render_status_surfaces_the_autonomy_brake(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The master kill-switch state is fleet-wide context: `OFF` (uppercased to catch
    # the eye) means the daemon is still crawling but self-modification is frozen.
    monkeypatch.setattr(config, "generator_dir", lambda: str(tmp_path))
    store = StateStore(":memory:")

    monkeypatch.setattr(config, "autonomy", lambda: True)
    assert "autonomy: on" in render_status([], store)

    monkeypatch.setattr(config, "autonomy", lambda: False)
    assert "autonomy: OFF" in render_status([], store)
    store.close()


def test_cmd_status_prints_and_returns_zero(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # cmd_status loads the registry + opens the configured store, prints the surface,
    # and exits 0. The rendering is unit-tested above; this pins the wiring.
    sites = tmp_path / "sites.yaml"
    sites.write_text("- slug: greenhouse\n  url: https://g.com\n")
    monkeypatch.setattr(config, "sites_path", lambda: str(sites))
    monkeypatch.setattr(config, "state_url", lambda: "sqlite:///:memory:")

    assert cmd_status(build_parser().parse_args(["status"])) == 0
    out = capsys.readouterr().out
    assert "greenhouse" in out
    assert "positions:" in out  # the §4.7 block is part of status


# -- positions / matches surfaces (§4.7/§6) ----------------------------------


def test_render_positions_aligns_score_title_company_location_workplace() -> None:
    store = StateStore(":memory:")
    _seed_position(
        store,
        url="https://acme.com/a",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote (EU)",
        workplace="remote",
        content_hash="hA",
        score=88,
    )
    _seed_position(
        store,
        url="https://acme.com/b",
        title="Data Scientist",
        company="Acme",
        location="Berlin",
        workplace="hybrid",
        content_hash="hB",  # unscored
    )
    rows = store.open_positions()
    lines = render_positions(rows)

    joined = "\n".join(lines)
    assert "88" in joined
    assert "Senior Backend Engineer" in joined
    assert "Acme" in joined
    assert "remote" in joined
    # An unscored position renders a dash in the score column rather than a number.
    unscored = next(line for line in lines if "Data Scientist" in line)
    assert unscored.strip().startswith("-")
    store.close()


def test_cmd_positions_lists_open_positions(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(db))
    store = StateStore(str(db))
    _seed_position(
        store,
        url="https://acme.com/a",
        title="Staff Engineer",
        content_hash="hA",
        score=91,
        reasons=["distributed systems"],
    )
    store.close()

    assert cmd_positions(build_parser().parse_args(["positions"])) == 0
    out = capsys.readouterr().out
    assert "Staff Engineer" in out and "Acme" in out and "91" in out


def test_cmd_positions_filters_by_min_score(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(db))
    store = StateStore(str(db))
    _seed_position(
        store,
        url="https://acme.com/hi",
        title="Great Match",
        content_hash="hi",
        score=92,
    )
    _seed_position(
        store,
        url="https://acme.com/lo",
        title="Weak Match",
        content_hash="lo",
        score=30,
    )
    store.close()

    assert (
        cmd_positions(build_parser().parse_args(["positions", "--min-score", "70"]))
        == 0
    )
    out = capsys.readouterr().out
    assert "Great Match" in out
    assert "Weak Match" not in out  # below the floor → filtered out


def test_cmd_positions_empty_says_so(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(db))
    StateStore(str(db)).close()  # empty store

    assert cmd_positions(build_parser().parse_args(["positions"])) == 0
    assert "no open positions" in capsys.readouterr().out


def test_render_matches_shows_score_verdict_and_reasons() -> None:
    store = StateStore(":memory:")
    _seed_position(
        store,
        url="https://acme.com/a",
        title="Principal Engineer",
        content_hash="hA",
        score=95,
        verdict="strong",
        reasons=["payments infra", "remote-first"],
    )
    rows = store.matches(70)
    lines = render_matches(rows)

    joined = "\n".join(lines)
    assert "95" in joined and "strong" in joined
    assert "Principal Engineer @ Acme" in joined
    assert "payments infra" in joined and "remote-first" in joined
    store.close()


def test_cmd_matches_ranks_by_score_and_honors_default_floor(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `matches` with no --min uses config.match_min_score() (the notify threshold): a
    # below-floor position is excluded, and results are ordered best-fit first.
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(db))
    store = StateStore(str(db))
    _seed_position(
        store,
        url="https://acme.com/top",
        title="Top Role",
        content_hash="t",
        score=96,
    )
    _seed_position(
        store,
        url="https://acme.com/mid",
        title="Mid Role",
        content_hash="m",
        score=78,
    )
    _seed_position(
        store,
        url="https://acme.com/low",
        title="Low Role",
        content_hash="l",
        score=40,  # below the default 70 floor
    )
    store.close()

    assert cmd_matches(build_parser().parse_args(["matches"])) == 0
    out = capsys.readouterr().out
    assert "Top Role" in out and "Mid Role" in out
    assert "Low Role" not in out
    assert out.index("Top Role") < out.index("Mid Role")  # score desc


def test_cmd_matches_empty_says_so(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "state.db"
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", str(db))
    StateStore(str(db)).close()

    assert cmd_matches(build_parser().parse_args(["matches", "--min", "80"])) == 0
    assert "no open positions scored >= 80" in capsys.readouterr().out


# -- rematch: the token-free §6 cache-drain ----------------------------------


def test_run_rematch_drains_pending_and_rescores_stale_verdicts(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # rematch is a token-free cache-drain keyed by the CURRENT profile_hash: a pending
    # position with a cached verdict is applied (scored); one without stays pending; an
    # already-scored position whose cache verdict differs is rescored; and one whose
    # cache misses under the current profile (its verdict predates a profile edit) is
    # parked pending for the next crawl to re-score.
    profile = tmp_path / "PROFILE.md"
    profile.write_text(_VALID_PROFILE)
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(profile))
    monkeypatch.setenv("JOBCRAWLER_MATCH_MODEL", "test/match-model")
    profile_hash = load_profile(str(profile)).profile_hash
    model = "test/match-model"

    store = StateStore(":memory:")
    # A: pending, cache HIT under the current profile → scored from cache.
    _seed_position(
        store, url="https://acme.com/a", title="A", content_hash="hA", pending=True
    )
    store.cache_match(
        "hA",
        profile_hash,
        model,
        PROMPT_VERSION,
        json.dumps(
            {"score": 85, "verdict": "strong", "reasons": ["fit"], "concerns": []}
        ),
    )
    # B: pending, NO cache entry → stays pending.
    _seed_position(
        store, url="https://acme.com/b", title="B", content_hash="hB", pending=True
    )
    # C: already scored 50, cache verdict differs (90) → rescored.
    _seed_position(
        store, url="https://acme.com/c", title="C", content_hash="hC", score=50
    )
    store.cache_match(
        "hC",
        profile_hash,
        model,
        PROMPT_VERSION,
        json.dumps(
            {"score": 90, "verdict": "strong", "reasons": ["now a fit"], "concerns": []}
        ),
    )
    # D: already scored 70, cache MISS under the current profile → parked pending.
    _seed_position(
        store, url="https://acme.com/d", title="D", content_hash="hD", score=70
    )

    assert _run_rematch(store) == 0
    out = capsys.readouterr().out
    assert "scored=1" in out and "rescored=1" in out
    assert "pending=2" in out and "cache_hits=2" in out

    by_url = {p.url: p for p in store.open_positions()}
    assert by_url["https://acme.com/a"].match_score == 85
    assert by_url["https://acme.com/a"].match_pending is False
    assert by_url["https://acme.com/b"].match_score is None
    assert by_url["https://acme.com/b"].match_pending is True
    assert by_url["https://acme.com/c"].match_score == 90  # rescored from cache
    assert by_url["https://acme.com/d"].match_pending is True  # stale verdict parked
    store.close()


def test_cmd_rematch_without_a_profile_exits_two(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # rematch scores against a profile; with none there is nothing to match, so it
    # fails loud (exit 2) rather than silently no-op.
    monkeypatch.setenv("JOBCRAWLER_STATE_URL", ":memory:")
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(tmp_path / "PROFILE.md"))  # missing
    monkeypatch.setenv("JOBCRAWLER_MATCH_MODEL", "test/match-model")

    assert cmd_rematch(build_parser().parse_args(["rematch"])) == 2


# -- profile view (§6) -------------------------------------------------------


def test_cmd_profile_prints_prefs_hash_and_body(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "PROFILE.md"
    profile.write_text(_VALID_PROFILE)
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(profile))
    expected_hash = load_profile(str(profile)).profile_hash

    assert cmd_profile(build_parser().parse_args(["profile"])) == 0
    out = capsys.readouterr().out
    assert expected_hash in out
    assert "disciplines:" in out and "backend" in out
    assert "workplace:" in out and "remote" in out
    assert "body:" in out and "chars" in out


def test_cmd_profile_missing_is_a_soft_miss(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(tmp_path / "PROFILE.md"))  # absent

    assert cmd_profile(build_parser().parse_args(["profile"])) == 1
    assert "no active profile" in capsys.readouterr().out


def test_cmd_profile_malformed_fails_loud(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "PROFILE.md"
    profile.write_text(_MALFORMED_PROFILE)
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(profile))

    assert cmd_profile(build_parser().parse_args(["profile"])) == 2
    assert "malformed" in capsys.readouterr().out


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
    # This is drift, not a cold-start: the site HAS a live crawler that broke, so the
    # failure-capture hook fires. (A crawler-less site is a new-site bootstrap, which is
    # not a learning signal and skips the hook.)
    monkeypatch.setattr("jobcrawler.cli._has_live_crawler", lambda slug: True)

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


def test_cmd_crawl_shares_one_matcher_across_sites(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # §6/§8: every crawl in a single invocation must share ONE matcher so the global
    # daily match cap and the match rate-limit hold. A per-crawl matcher would let N
    # concurrent sites each spend independently → N× the token budget.
    seen: list[object] = []
    sentinel = object()

    def fake_crawl_one(
        site: Site, *, store: StateStore, matcher: object = None
    ) -> CrawlSummary:
        seen.append(matcher)
        return CrawlSummary(
            site.slug,
            positions_found=0,
            new_positions=0,
            changed_positions=0,
            closed_positions=0,
            errors=0,
            pages_fetched=0,
        )

    monkeypatch.setenv("JOBCRAWLER_STATE_URL", ":memory:")
    monkeypatch.setenv(
        "JOBCRAWLER_PROFILE", str(tmp_path / "PROFILE.md")
    )  # missing → warn
    monkeypatch.setattr(
        "jobcrawler.cli.load_sites",
        lambda _path: [
            Site(slug="a", url="https://a.com"),
            Site(slug="b", url="https://b.com"),
        ],
    )
    monkeypatch.setattr("jobcrawler.cli.build_matcher", lambda store: sentinel)
    monkeypatch.setattr("jobcrawler.cli.crawl_one", fake_crawl_one)

    assert cmd_crawl(build_parser().parse_args(["crawl", "--all"])) == 0
    assert len(seen) == 2
    assert seen[0] is sentinel
    assert seen[0] is seen[1]  # the SAME instance threaded through, not one per site


def test_cmd_crawl_prints_position_counts(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # cmd_crawl reports the §4.7 lifecycle deltas per site (found/new/changed/closed/
    # matched/pending/incomplete/errors) from the CrawlSummary the runner returns.
    def fake_crawl_one(
        site: Site, *, store: StateStore, matcher: object = None
    ) -> CrawlSummary:
        return CrawlSummary(
            site.slug,
            positions_found=12,
            new_positions=3,
            changed_positions=1,
            closed_positions=2,
            errors=0,
            pages_fetched=5,
            incomplete=1,
            matched=4,
            match_pending=2,
        )

    monkeypatch.setenv("JOBCRAWLER_STATE_URL", ":memory:")
    monkeypatch.setenv("JOBCRAWLER_PROFILE", str(tmp_path / "PROFILE.md"))
    monkeypatch.setattr(
        "jobcrawler.cli.load_sites",
        lambda _path: [Site(slug="acme", url="https://acme.com")],
    )
    monkeypatch.setattr("jobcrawler.cli.build_matcher", lambda store: None)
    monkeypatch.setattr("jobcrawler.cli.crawl_one", fake_crawl_one)

    assert cmd_crawl(build_parser().parse_args(["crawl", "--all"])) == 0
    out = capsys.readouterr().out
    assert "acme:" in out
    assert "found=12" in out and "new=3" in out and "closed=2" in out
    assert "matched=4" in out and "pending=2" in out


def test_crawl_one_isolates_a_hanging_committed_crawler(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # The #52 guarantee: a committed crawler that loops without fetching never trips
    # the page budget, so run in-process it would wedge the daemon (and the whole
    # fleet) forever. crawl_one must execute it in the bounded §4.4 subprocess — a
    # hang comes back as a finalized failed run (0 positions, >=1 error), never a hung
    # call, and nothing is tracked.
    (tmp_path / "hang.py").write_text(
        'SITE = "https://h.com"\ndef crawl(ctx):\n    while True:\n        pass\n'
    )
    monkeypatch.setenv("JOBCRAWLER_CRAWL_TIMEOUT", "1")
    monkeypatch.setenv("JOBCRAWLER_CRAWL_CPU_SECONDS", "1")
    store = StateStore(":memory:")

    summary = crawl_one(
        Site(slug="hang", url="https://h.com", name="Hang"),
        store=store,
        matcher=None,  # crawl-only; the hang bounds out before any matching
        crawlers_dir=tmp_path,
    )

    assert summary.positions_found == 0
    assert summary.errors >= 1
    history = store.runs_for("hang")
    assert len(history) == 1 and history[0].finished is not None
    store.close()


def test_probe_counts_complete_positions_through_isolation(tmp_path: Path) -> None:
    # _probe is the self-healer's confirm-a-real-failure re-run; it must run the
    # committed script through the same bounded subprocess and count only complete
    # positions (no upsert, no state). A healthy script reports its complete count.
    (tmp_path / "good.py").write_text(
        "from jobcrawler.runtime.contract import Job\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        "    for i in range(3):\n"
        "        yield Job(\n"
        '            url=f"{SITE}/a{i}",\n'
        '            title=f"Engineer {i}",\n'
        '            company="Acme",\n'
        '            location="Remote",\n'
        '            description="x" * 500,\n'
        '            source="good",\n'
        "        )\n"
    )
    assert _probe("good", crawlers_dir=tmp_path) == 3


def test_probe_is_zero_when_the_script_is_missing(tmp_path: Path) -> None:
    # A site whose committed script is missing probes to 0 (a confirmed failure →
    # regenerate), never an exception that aborts the self-heal loop.
    assert _probe("ghost", crawlers_dir=tmp_path) == 0


def test_validate_one_runs_the_sandbox(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "from jobcrawler.runtime.contract import Job\n"
        'SITE = "https://example.com"\n'
        "def crawl(ctx):\n"
        "    for i in range(3):\n"
        "        yield Job(\n"
        '            url=f"{SITE}/a{i}",\n'
        '            title=f"Engineer {i}",\n'
        '            company="Acme",\n'
        '            location="Remote",\n'
        '            description="x" * 500,\n'
        '            source="good",\n'
        "        )\n"
    )
    result = validate_one("good", crawlers_dir=tmp_path)
    assert result.exception is None
    assert result.item_count == 3


def test_cmd_validate_reports_positions(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `validate` labels the sandbox count as positions (not items) and exits 0 when the
    # crawler yields at least one; the sandbox itself is stubbed here (unit-tested above).
    from jobcrawler import cli
    from jobcrawler.generate.scoring import CandidateResult

    (tmp_path / "good.py").write_text(
        'SITE = "https://example.com"\ndef crawl(ctx):\n    return []\n'
    )
    monkeypatch.setattr(
        cli,
        "validate_one",
        lambda slug, **_kw: CandidateResult(item_count=4, pages_fetched=2),
    )

    assert cmd_validate(build_parser().parse_args(["validate", "good"])) == 0
    out = capsys.readouterr().out
    assert "4 positions" in out and "items" not in out


def test_validate_one_previews_production_politeness(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # `jobcrawl validate` is the operator's dry-run preview of what the scheduled
    # crawl will do, so it must run the committed crawler through the SAME §4.5-polite
    # live ctx (per-host pacer + robots) production uses. Otherwise a crawler can
    # validate clean yet under-collect in production, where robots blocks a path the
    # bare validation ctx happily fetched (a train/serve skew).
    from jobcrawler import cli
    from jobcrawler.generate.scoring import CandidateResult

    (tmp_path / "good.py").write_text(
        'SITE = "https://example.com"\ndef crawl(ctx):\n    return []\n'
    )
    captured: dict[str, object] = {}

    def fake_validate(source: str, **kwargs: object) -> CandidateResult:
        captured.update(kwargs)
        return CandidateResult(item_count=0, pages_fetched=0)

    monkeypatch.setattr(cli, "validate_crawler", fake_validate)
    cli.validate_one("good", crawlers_dir=tmp_path)
    assert captured["polite"] is True


def test_crawlers_dir_default_is_config_driven() -> None:
    # The module default must come from config.crawlers_dir() so a read-only Nix-store
    # deploy can relocate the writable crawlers dir via JOBCRAWLER_CRAWLERS_DIR (§11).
    from jobcrawler import cli

    assert cli._CRAWLERS_DIR == Path(config.crawlers_dir())
