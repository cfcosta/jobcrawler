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
