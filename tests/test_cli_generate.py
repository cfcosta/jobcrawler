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
    # Zero postings never clears the floor — an empty board yield is always treated
    # as a broken crawler, not a quiet-but-healthy one (that distinction lives in
    # §4.8 health, not the generation gate).
    none = 'SITE = "https://example.com"\ndef crawl(ctx):\n    return iter(())\n'
    ok, reason = gate_winner(_pred(), none, _HOSTS)
    assert not ok and "completeness" in reason


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
        site: Site, root_lm: object, sub_lm: object, *, mode: str = "off"
    ) -> RegenOutcome:
        seen["mode"] = mode
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
