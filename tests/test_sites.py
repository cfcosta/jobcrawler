"""Tests for the site registry loader (§4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobcrawler.schedule import Site as CadenceSite
from jobcrawler.sites import Site, find_site, load_sites

_YAML = """
- slug: gitlab
  name: GitLab
  url: https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true
  company: GitLab
  render: static
  schedule: 30m
- slug: remoteok
  url: https://remoteok.com/api
  enumerates_all: false
  enabled: false
"""


def test_load_sites_parses_entries(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text(_YAML)
    sites = load_sites(str(path))
    assert [s.slug for s in sites] == ["gitlab", "remoteok"]
    assert sites[0].schedule == "30m"
    assert sites[0].name == "GitLab"
    assert sites[0].render == "static"
    # single-company board: `company` set, and enumerates_all defaults true.
    assert sites[0].company == "GitLab"
    assert sites[0].enumerates_all is True
    assert sites[1].enabled is False
    assert sites[1].render == "auto"  # default
    # aggregator: no company (each posting names its own), windowed source.
    assert sites[1].company is None
    assert sites[1].enumerates_all is False


def test_load_sites_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text("")
    assert load_sites(str(path)) == []


def test_find_site(tmp_path: Path) -> None:
    path = tmp_path / "sites.yaml"
    path.write_text(_YAML)
    sites = load_sites(str(path))
    ok = find_site(sites, "remoteok")
    assert ok is not None
    assert ok.slug == "remoteok"
    assert find_site(sites, "nope") is None


# -- §4.1 job-domain registry fields (company, enumerates_all) ----------------


def test_new_fields_default_when_omitted() -> None:
    # An entry that names neither field is a whole-board, multi-company source by
    # default: no forced company, close-missing diff enabled.
    site = Site.model_validate({"slug": "acme", "url": "https://acme.example"})
    assert site.company is None
    assert site.enumerates_all is True


def test_company_and_enumerates_all_parse() -> None:
    site = Site.model_validate(
        {
            "slug": "acme",
            "url": "https://acme.example",
            "company": "Acme Inc",
            "enumerates_all": False,
        }
    )
    assert site.company == "Acme Inc"
    assert site.enumerates_all is False


def test_non_bool_enumerates_all_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Site.model_validate(
            {
                "slug": "acme",
                "url": "https://acme.example",
                "enumerates_all": "sometimes",
            }
        )


def test_registry_site_is_the_scheduler_model() -> None:
    # sites.py is the loader over the canonical §4.1 model, not a shadow of it: both
    # import paths must resolve to the SAME class so the loader never drifts from the
    # cli/generate/runner consumers that import Site from jobcrawler.schedule.
    assert Site is CadenceSite


# -- §4.9 fleet adaptive default (JOBCRAWLER_ADAPTIVE_DEFAULT) ----------------

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
