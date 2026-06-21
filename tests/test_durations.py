"""Tests for duration-string parsing (§4.1 schedules, §4.10 clearance TTL)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from bizup_autocrawler.durations import parse_duration


def test_parses_each_unit() -> None:
    assert parse_duration("45s") == timedelta(seconds=45)
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("6h") == timedelta(hours=6)
    assert parse_duration("1d") == timedelta(days=1)


def test_tolerates_whitespace_and_case() -> None:
    assert parse_duration("  30M ") == timedelta(minutes=30)


def test_rejects_invalid() -> None:
    for bad in ["", "30", "h", "1w", "5 minutes", "-1m"]:
        with pytest.raises(ValueError):
            parse_duration(bad)
