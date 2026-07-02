"""Tests for duration-string parsing (§4.1 schedules, §4.10 clearance TTL)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobcrawler.durations import parse_duration, parse_retry_after

_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


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


# -- Retry-After parsing (§4.5 transient-fetch backoff) ----------------------


def test_retry_after_integer_seconds() -> None:
    # RFC 9110 delay-seconds form — the common case for 429/503.
    assert parse_retry_after("120", _NOW) == 120.0
    assert parse_retry_after("  0 ", _NOW) == 0.0


def test_retry_after_http_date_is_seconds_from_now() -> None:
    # The other RFC form: an absolute HTTP-date, measured against the supplied clock.
    assert parse_retry_after("Thu, 01 Jan 2026 00:01:30 GMT", _NOW) == 90.0


def test_retry_after_past_date_clamps_to_zero() -> None:
    # A date already elapsed means "retry now", never a negative wait.
    assert parse_retry_after("Wed, 31 Dec 2025 23:59:00 GMT", _NOW) == 0.0


def test_retry_after_unparseable_is_none() -> None:
    # Neither form → None, so the caller falls back to its own exponential backoff.
    assert parse_retry_after("soon", _NOW) is None
    assert parse_retry_after("", _NOW) is None
