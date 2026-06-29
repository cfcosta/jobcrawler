"""Tests for the site registry loader (§4.1)."""

from __future__ import annotations

from pathlib import Path

from bizup_autocrawler.sites import find_site, load_sites

_YAML = """
- slug: techcrunch
  name: TechCrunch
  url: https://techcrunch.com
  schedule: 30m
- slug: theverge
  url: https://theverge.com
  enabled: false
"""


def test_load_sites_parses_entries(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text(_YAML)
    sites = load_sites(str(path))
    assert [s.slug for s in sites] == ["techcrunch", "theverge"]
    assert sites[0].schedule == "30m"
    assert sites[0].name == "TechCrunch"
    assert sites[1].enabled is False
    assert sites[1].render == "auto"  # default


def test_load_sites_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text("")
    assert load_sites(str(path)) == []


def test_find_site(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text(_YAML)
    sites = load_sites(str(path))
    verge = find_site(sites, "theverge")
    assert verge is not None
    assert verge.slug == "theverge"
    assert find_site(sites, "nope") is None


# -- §4.9 fleet adaptive default (AUTOCRAWLER_ADAPTIVE_DEFAULT) ----------------

_MODES_YAML = """
- slug: plain
  url: https://plain.example
- slug: fixed
  url: https://fixed.example
  schedule: 30m
- slug: pinned_off
  url: https://pinned.example
  adaptive: false
- slug: pinned_on
  url: https://on.example
  adaptive: true
"""


def test_fleet_adaptive_default_only_applies_to_unpinned_sites(tmp_path: Path) -> None:
    # With the fleet default on, a site that pins neither schedule nor adaptive becomes
    # adaptive; any explicit cadence choice on the entry still wins.
    path = tmp_path / "sites.yaml"
    path.write_text(_MODES_YAML)
    sites = {s.slug: s for s in load_sites(str(path), adaptive_default=True)}
    assert sites["plain"].adaptive is True  # inherited the fleet default
    assert sites["fixed"].adaptive is False  # explicit schedule wins
    assert sites["fixed"].schedule == "30m"
    assert sites["pinned_off"].adaptive is False  # explicit adaptive: false wins
    assert sites["pinned_on"].adaptive is True


def test_fleet_default_off_leaves_unpinned_sites_fixed(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text(_MODES_YAML)
    sites = {s.slug: s for s in load_sites(str(path), adaptive_default=False)}
    assert sites["plain"].adaptive is False  # default cadence mode is fixed
    assert sites["pinned_on"].adaptive is True  # an opt-in still holds
