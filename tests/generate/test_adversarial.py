"""Tests for §8 adversarial admission (cross-validate claimed positions)."""

from __future__ import annotations

from jobcrawler.generate.adversarial import cross_validate

_HOSTS = {"example.com"}


def _s(url: str, title: str, description: str) -> dict[str, str]:
    return {"url": url, "title": title, "description": description}


def test_clean_positions_pass() -> None:
    samples = [
        _s("https://example.com/a", "Senior Backend Engineer", "Build our APIs..."),
        _s("https://example.com/b", "Frontend Engineer", "Own the web app..."),
    ]
    titles = {
        "https://example.com/a": "Senior Backend Engineer — Example Careers",
        "https://example.com/b": "Frontend Engineer — Example Careers",
    }
    assert cross_validate(samples, live_titles=titles, allowed_hosts=_HOSTS).passed


def test_off_domain_url_fails() -> None:
    result = cross_validate(
        [_s("https://evil.com/a", "X", "b")], live_titles={}, allowed_hosts=_HOSTS
    )
    assert not result.passed
    assert result.off_domain == 1


def test_title_mismatch_fails() -> None:
    samples = [_s("https://example.com/a", "Totally Wrong Role", "b")]
    titles = {"https://example.com/a": "Staff Data Engineer — Example Careers"}
    result = cross_validate(samples, live_titles=titles, allowed_hosts=_HOSTS)
    assert not result.passed
    assert result.title_mismatches == 1


def test_verbatim_duplicate_descriptions_fail() -> None:
    # The index page (or an About page) yielded as every "posting": distinct roles
    # never share a byte-identical whole description, so this is a fabrication signal.
    samples = [
        _s(f"https://example.com/{i}", f"Role {i}", "SAME ABOUT PAGE") for i in range(3)
    ]
    result = cross_validate(samples, live_titles={}, allowed_hosts=_HOSTS)
    assert not result.passed
    assert result.duplicate_descriptions == 2


def test_shared_boilerplate_but_distinct_descriptions_pass() -> None:
    # Company boards repeat an EEO/About footer across postings; as long as the
    # whole description differs per role, adversarial admission does not fire (the
    # §6 gate owns shared-block stripping — this check is only for identical pages).
    footer = "\n\nExample Inc is an equal-opportunity employer."
    samples = [
        _s("https://example.com/a", "Backend Engineer", "Design services." + footer),
        _s("https://example.com/b", "Mobile Engineer", "Ship the iOS app." + footer),
    ]
    result = cross_validate(samples, live_titles={}, allowed_hosts=_HOSTS)
    assert result.passed
    assert result.duplicate_descriptions == 0


def test_no_positions_fails() -> None:
    assert not cross_validate([], live_titles={}, allowed_hosts=_HOSTS).passed
