"""Duration-string parsing — ``"30m"`` / ``"6h"`` → ``timedelta``.

Shared by site schedules (§4.1, the simple-cadence forms) and the §4.10 clearance
TTL. Cron expressions (also valid for ``schedule``) are the scheduler's concern,
not this helper.
"""

from __future__ import annotations

import re
from datetime import timedelta

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
