"""Time-string parsing for the service.

The config-facing forms — ``"30m"`` / ``"6h"`` → ``timedelta`` — are shared by site
schedules (§4.1, the simple-cadence forms) and the §4.10 clearance TTL. Cron
expressions (also valid for ``schedule``) are the scheduler's concern, not this
helper. The wire-facing :func:`parse_retry_after` reads an HTTP ``Retry-After`` for
the §4.5 transient-fetch backoff.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
_PATTERN = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)


def parse_duration(value: str) -> timedelta:
    """Parse ``<int><s|m|h|d>`` into a ``timedelta`` (case/whitespace tolerant)."""
    match = _PATTERN.match(value)
    if match is None:
        raise ValueError(
            f"invalid duration: {value!r} (use forms like 45s, 30m, 6h, 1d)"
        )
    return timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})


def parse_retry_after(value: str, now: datetime) -> float | None:
    """Parse an HTTP ``Retry-After`` value into seconds from ``now`` (RFC 9110 §10.2.3).

    Two wire forms: a non-negative integer of delay-seconds, or an absolute HTTP-date
    measured against the supplied ``now`` (an already-elapsed date clamps to ``0.0``).
    Returns ``None`` when the value parses as neither, so the caller falls back to its
    own backoff rather than trusting a malformed header.
    """
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:  # a naive HTTP-date is UTC by spec
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - now).total_seconds())
