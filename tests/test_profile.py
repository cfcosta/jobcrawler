"""Tests for PROFILE.md loading (§8 host-side profile).

Cover the two halves of the file — the machine-readable frontmatter prefs (the §6
prefilter reads these) and the free markdown body (the LLM matching context) — plus
the content hash that keys the match cache. Loud validation is a feature: an unknown
enum value or a missing file must fail with a nameable error at load time, not match
nothing silently. All fixtures are written to ``tmp_path`` so the tests never depend on
the committed PROFILE.md (which a sibling may still be editing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobcrawler.profile import (
    Profile,
    ProfileNotFound,
    ProfilePrefs,
    SalaryFloor,
    load_profile,
)

_FULL = """\
---
disciplines:
  - backend
  - fullstack
levels:
  - senior
  - staff_plus
workplace:
  - remote
locations:
  - São Paulo, Brazil
languages:
  - en
  - pt
exclude_keywords:
  - clearance required
exclude_companies: []
exclude_locations:
  - remote, us
  - united states
min_salary:
  amount: 8000
  currency: BRL
  period: month
---

# Jane — Senior Backend Engineer

Distributed systems and payments. Remote only, from São Paulo.
"""


def _write(tmp_path: Path, text: str) -> str:
    path = tmp_path / "PROFILE.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_load_parses_full_frontmatter(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path, _FULL))
    assert isinstance(profile, Profile)
    prefs = profile.prefs
    assert prefs.disciplines == frozenset({"backend", "fullstack"})
    assert prefs.levels == frozenset({"senior", "staff_plus"})
    assert prefs.workplace == frozenset({"remote"})
    assert prefs.locations == ("São Paulo, Brazil",)
    assert prefs.languages == ("en", "pt")
    assert prefs.exclude_keywords == ("clearance required",)
    assert prefs.exclude_companies == ()
    assert prefs.exclude_locations == ("remote, us", "united states")


def test_body_is_extracted_without_the_frontmatter(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path, _FULL))
    assert profile.body.startswith("# Jane — Senior Backend Engineer")
    assert "distributed systems" in profile.body.lower()
    # The frontmatter fences/keys must not leak into the LLM context.
    assert "disciplines:" not in profile.body
    assert "---" not in profile.body


def test_min_salary_parsed_into_salary_floor(tmp_path: Path) -> None:
    prefs = load_profile(_write(tmp_path, _FULL)).prefs
    assert prefs.min_salary == SalaryFloor(
        amount=8000.0, currency="BRL", period="month"
    )


def test_empty_frontmatter_yields_no_constraints(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path, "---\n---\n\nJust a body.\n"))
    assert profile.prefs == ProfilePrefs(
        disciplines=frozenset(),
        levels=frozenset(),
        workplace=frozenset(),
        locations=(),
        min_salary=None,
        languages=(),
        exclude_keywords=(),
        exclude_companies=(),
        exclude_locations=(),
    )
    assert profile.body == "Just a body."


def test_missing_keys_default_to_empty(tmp_path: Path) -> None:
    # Only disciplines given: every other pref must come back empty (no constraint).
    profile = load_profile(_write(tmp_path, "---\ndisciplines: [backend]\n---\nBody\n"))
    assert profile.prefs.disciplines == frozenset({"backend"})
    assert profile.prefs.levels == frozenset()
    assert profile.prefs.workplace == frozenset()
    assert profile.prefs.min_salary is None
    assert profile.prefs.exclude_companies == ()


def test_no_frontmatter_is_an_all_body_profile(tmp_path: Path) -> None:
    profile = load_profile(_write(tmp_path, "# Just markdown\n\nNo prefs at all.\n"))
    assert profile.prefs.disciplines == frozenset()
    assert profile.body.startswith("# Just markdown")


def test_scalar_pref_is_accepted_as_a_single_value(tmp_path: Path) -> None:
    # An operator may write `workplace: remote` instead of a one-item list.
    prefs = load_profile(_write(tmp_path, "---\nworkplace: remote\n---\nb\n")).prefs
    assert prefs.workplace == frozenset({"remote"})


def test_enum_values_are_case_insensitive(tmp_path: Path) -> None:
    prefs = load_profile(
        _write(tmp_path, "---\ndisciplines: [Backend]\n---\nb\n")
    ).prefs
    assert prefs.disciplines == frozenset({"backend"})


def test_bad_discipline_raises_valueerror_naming_the_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="wizardry"):
        load_profile(_write(tmp_path, "---\ndisciplines: [wizardry]\n---\nb\n"))


def test_bad_level_raises_valueerror_naming_the_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlord"):
        load_profile(_write(tmp_path, "---\nlevels: [overlord]\n---\nb\n"))


def test_bad_workplace_raises_valueerror_naming_the_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="moon"):
        load_profile(_write(tmp_path, "---\nworkplace: [moon]\n---\nb\n"))


def test_bad_salary_period_raises(tmp_path: Path) -> None:
    text = "---\nmin_salary:\n  amount: 100\n  currency: usd\n  period: fortnight\n---\nb\n"
    with pytest.raises(ValueError, match="period"):
        load_profile(_write(tmp_path, text))


def test_salary_without_amount_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="amount"):
        load_profile(_write(tmp_path, "---\nmin_salary:\n  currency: usd\n---\nb\n"))


def test_unclosed_frontmatter_fence_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="closed"):
        load_profile(
            _write(tmp_path, "---\ndisciplines: [backend]\nno closing fence\n")
        )


def test_missing_file_raises_profile_not_found_with_hint(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFound) as excinfo:
        load_profile(str(tmp_path / "does-not-exist.md"))
    # It is a FileNotFoundError subclass carrying an actionable create-it hint.
    assert isinstance(excinfo.value, FileNotFoundError)
    assert "PROFILE.md" in str(excinfo.value)


def test_profile_hash_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    first = load_profile(_write(tmp_path, _FULL))
    again = load_profile(_write(tmp_path, _FULL))
    assert first.profile_hash == again.profile_hash  # deterministic

    # A body-only edit (new LLM context) must still change the hash so cached verdicts
    # lazily invalidate — the whole point of hashing the raw text, not just the prefs.
    edited = load_profile(_write(tmp_path, _FULL + "\nExtra context sentence.\n"))
    assert edited.profile_hash != first.profile_hash


def test_profile_hash_ignores_cosmetic_whitespace(tmp_path: Path) -> None:
    # Trailing whitespace / CRLF churn is not a semantic change and must not rescore.
    base = load_profile(
        _write(tmp_path, "---\ndisciplines: [backend]\n---\nBody line\n")
    )
    crlf = load_profile(
        _write(tmp_path, "---\r\ndisciplines: [backend]\r\n---\r\nBody line   \r\n")
    )
    assert base.profile_hash == crlf.profile_hash
