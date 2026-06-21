"""Tests for §8 adversarial admission (cross-validate claimed items)."""

from __future__ import annotations

from bizup_autocrawler.generate.adversarial import cross_validate

_HOSTS = {"example.com"}


def _s(url: str, title: str, body: str) -> dict[str, str]:
    return {"url": url, "title": title, "body": body}


def test_clean_items_pass() -> None:
    samples = [
        _s("https://example.com/a", "Alpha", "body a"),
        _s("https://example.com/b", "Beta", "body b"),
    ]
    titles = {
        "https://example.com/a": "Alpha — Example",
        "https://example.com/b": "Beta — Example",
    }
    assert cross_validate(samples, live_titles=titles, allowed_hosts=_HOSTS).passed


def test_off_domain_url_fails() -> None:
    result = cross_validate(
        [_s("https://evil.com/a", "X", "b")], live_titles={}, allowed_hosts=_HOSTS
    )
    assert not result.passed
    assert result.off_domain == 1


def test_title_mismatch_fails() -> None:
    samples = [_s("https://example.com/a", "Totally Wrong", "b")]
    titles = {"https://example.com/a": "The Actual Headline"}
    result = cross_validate(samples, live_titles=titles, allowed_hosts=_HOSTS)
    assert not result.passed
    assert result.title_mismatches == 1


def test_boilerplate_duplicate_bodies_fail() -> None:
    samples = [_s(f"https://example.com/{i}", f"T{i}", "SAME BODY") for i in range(3)]
    result = cross_validate(samples, live_titles={}, allowed_hosts=_HOSTS)
    assert not result.passed
    assert result.duplicate_bodies == 2


def test_no_items_fails() -> None:
    assert not cross_validate([], live_titles={}, allowed_hosts=_HOSTS).passed
