"""Tests for the personal application-tracking policy (§16).

``tracking`` is the host-side status policy the store deliberately does *not* own: the
valid set, display order, labels/colours, and which stages imply an application was
submitted. ``state.py`` persists ``track_status`` as an opaque string; these tests pin the
policy that governs what the web UI is allowed to write and how it stamps ``applied_at``.
"""

from __future__ import annotations

import pytest

from jobcrawler import tracking
from jobcrawler.tracking import TrackStatus


def test_default_is_new() -> None:
    # Mirrors the ``track_status TEXT NOT NULL DEFAULT 'new'`` column default.
    assert tracking.DEFAULT is TrackStatus.NEW
    assert TrackStatus.NEW.value == "new"


def test_strenum_member_is_its_string() -> None:
    # StrEnum: a member *is* the stored string, so no ``.value`` juggling at the SQL seam.
    assert TrackStatus.APPLIED == "applied"
    assert f"{TrackStatus.OFFER}" == "offer"


def test_order_covers_every_status_once() -> None:
    assert set(tracking.ORDER) == set(TrackStatus)
    assert len(tracking.ORDER) == len(TrackStatus)


def test_pipeline_and_terminal_partition_the_enum() -> None:
    # Every status is either an active pursuit or a terminal outcome — never both, never
    # neither. The dashboard funnel relies on this partition.
    assert tracking.PIPELINE.isdisjoint(tracking.TERMINAL)
    assert tracking.PIPELINE | tracking.TERMINAL == set(TrackStatus)


def test_terminal_membership() -> None:
    assert tracking.TERMINAL == {
        TrackStatus.ACCEPTED,
        TrackStatus.REJECTED,
        TrackStatus.WITHDRAWN,
    }


@pytest.mark.parametrize("raw", ["new", "interested", "applied", "offer", "withdrawn"])
def test_parse_accepts_known_values(raw: str) -> None:
    assert tracking.parse(raw) == TrackStatus(raw)


def test_parse_rejects_unknown_and_lists_options() -> None:
    with pytest.raises(ValueError) as exc:
        tracking.parse("bogus")
    msg = str(exc.value)
    assert "bogus" in msg
    # The error enumerates the valid set so a caller/UI can surface it.
    assert "interviewing" in msg


def test_parse_is_exact_not_lenient() -> None:
    # No case-folding / trimming — the UI submits canonical values, and a sloppy match
    # would let a near-miss slip into the store.
    for bad in ["Applied", "APPLIED", " applied ", ""]:
        with pytest.raises(ValueError):
            tracking.parse(bad)


def test_is_valid_matches_parse() -> None:
    assert tracking.is_valid("applied")
    assert not tracking.is_valid("Applied")
    assert not tracking.is_valid("nope")


@pytest.mark.parametrize(
    "status,expected",
    [
        (TrackStatus.NEW, False),
        (TrackStatus.INTERESTED, False),
        (TrackStatus.APPLIED, True),
        (TrackStatus.INTERVIEWING, True),
        (TrackStatus.OFFER, True),
        (TrackStatus.ACCEPTED, True),
        # rejected/withdrawn deliberately excluded: you can bail before ever applying.
        (TrackStatus.REJECTED, False),
        (TrackStatus.WITHDRAWN, False),
    ],
)
def test_implies_applied(status: TrackStatus, expected: bool) -> None:
    assert tracking.implies_applied(status) is expected
    # accepts the raw string form too (what flows off the wire)
    assert tracking.implies_applied(status.value) is expected


def test_is_terminal() -> None:
    assert tracking.is_terminal(TrackStatus.REJECTED)
    assert tracking.is_terminal("accepted")
    assert not tracking.is_terminal(TrackStatus.APPLIED)


@pytest.mark.parametrize(
    "current,expected",
    [
        (TrackStatus.NEW, True),
        # every non-new stage is unreachable via the `i` keybinding — including the
        # terminal ones, which is the whole point (no reviving rejected/withdrawn rows).
        (TrackStatus.INTERESTED, False),
        (TrackStatus.APPLIED, False),
        (TrackStatus.INTERVIEWING, False),
        (TrackStatus.OFFER, False),
        (TrackStatus.ACCEPTED, False),
        (TrackStatus.REJECTED, False),
        (TrackStatus.WITHDRAWN, False),
    ],
)
def test_promotable_to_interested(current: TrackStatus, expected: bool) -> None:
    assert tracking.promotable_to_interested(current) is expected
    # accepts the raw stored string too (what the endpoint reads off the record)
    assert tracking.promotable_to_interested(current.value) is expected


def test_label_known_and_legacy_fallback() -> None:
    assert tracking.label("interviewing") == "Interviewing"
    # An unknown/legacy value must not blow up the template — Title Case fallback.
    assert tracking.label("on_hold") == "On Hold"


def test_css_token_known_and_unknown() -> None:
    assert tracking.css_token("rejected") == "danger"
    assert tracking.css_token("applied") == "progress"
    assert tracking.css_token("mystery") == "neutral"


def test_choices_shape_and_order() -> None:
    ch = tracking.choices()
    assert ch[0] == ("new", "New", "neutral")
    assert [value for value, _, _ in ch] == [s.value for s in tracking.ORDER]
    # every row is (value, label, token) and every token is a known palette slot
    palette = {
        "neutral",
        "info",
        "progress",
        "warn",
        "good",
        "success",
        "danger",
        "muted",
    }
    assert all(token in palette for _, _, token in ch)
