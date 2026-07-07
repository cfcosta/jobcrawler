"""Tests for the operator tracking axis on the store (§16).

The crawler owns the ``open``/``closed`` lifecycle and the ``match_*`` verdict; the human
owns an orthogonal axis — a personal pipeline stage, an ``applied_at`` date, free-text
notes, and a ``dismissed`` hide flag. These columns are store-owned (never taken from a
crawler's ``fields`` dict, like ``match_*``) so a re-crawl can't clobber them, and the
store persists ``track_status`` as an opaque string: valid-set/transition policy lives
host-side in ``tracking``. These tests pin that the two axes stay independent and that
``applied_at`` is stamped exactly once.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

from jobcrawler.state import PositionRecord, StateStore

FIELDS: dict[str, object] = {
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "location": "Remote",
}


class _MutableClock:
    """A hand-advanced clock so a test can prove ``applied_at``/``track_updated_at`` move."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def store() -> Iterator[StateStore]:
    s = StateStore(":memory:")
    yield s
    s.close()


def _seed(
    store: StateStore,
    site: str = "acme",
    url: str = "https://acme.test/a",
    **extra: object,
) -> None:
    store.upsert_position(
        site, url, fields={**FIELDS, **extra}, content_hash=f"h:{site}:{url}"
    )


def _get(
    store: StateStore, site: str = "acme", url: str = "https://acme.test/a"
) -> PositionRecord:
    """Fetch a position that must exist (asserts non-None so callers read attrs cleanly)."""
    p = store.get_position(site, url)
    assert p is not None
    return p


# -- defaults & get_position -------------------------------------------------


def test_new_position_defaults(store: StateStore) -> None:
    _seed(store)
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None
    assert p.track_status == "new"
    assert p.applied_at is None
    assert p.notes is None
    assert p.dismissed is False
    assert p.track_updated_at is None


def test_get_position_missing_is_none(store: StateStore) -> None:
    assert store.get_position("acme", "https://nope.test/x") is None


# -- set_track_status / applied_at stamping ----------------------------------


def test_set_track_status_persists_opaque_string(store: StateStore) -> None:
    _seed(store)
    # The store never validates the value — host policy does. It stores what it is given.
    store.set_track_status("acme", "https://acme.test/a", "interested")
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None and p.track_status == "interested"
    assert p.track_updated_at is not None  # any touch stamps the update time


def test_set_track_status_stamps_applied_once(store: StateStore) -> None:
    clk = _MutableClock(datetime(2026, 7, 1, tzinfo=timezone.utc))
    s = StateStore(":memory:", clock=clk)
    try:
        _seed(s)
        # First application-implying stage stamps applied_at from the clock.
        s.set_track_status("acme", "https://acme.test/a", "applied", stamp_applied=True)
        first = _get(s).applied_at
        assert first == datetime(2026, 7, 1, tzinfo=timezone.utc)

        # Advancing to a later stage must NOT overwrite the original apply date (COALESCE).
        clk.now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        s.set_track_status(
            "acme", "https://acme.test/a", "interviewing", stamp_applied=True
        )
        p = s.get_position("acme", "https://acme.test/a")
        assert p is not None
        assert p.applied_at == first  # unchanged
        assert p.track_updated_at == clk.now  # but the touch time advanced
    finally:
        s.close()


def test_moving_back_down_funnel_keeps_applied_at(store: StateStore) -> None:
    _seed(store)
    store.set_track_status("acme", "https://acme.test/a", "applied", stamp_applied=True)
    # A rejection does not imply applied, so the caller passes stamp_applied=False; the
    # already-stamped date must survive.
    store.set_track_status(
        "acme", "https://acme.test/a", "rejected", stamp_applied=False
    )
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None and p.applied_at is not None and p.track_status == "rejected"


def test_track_status_never_touches_lifecycle_or_match(store: StateStore) -> None:
    _seed(store)
    store.set_match(
        "acme", "https://acme.test/a", score=88, verdict="strong", reasons="[]"
    )
    store.set_track_status("acme", "https://acme.test/a", "applied", stamp_applied=True)
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None
    # orthogonal axes: the crawler lifecycle and the match verdict are untouched
    assert p.status == "open"
    assert p.closed_at is None
    assert p.match_score == 88
    assert p.match_verdict == "strong"
    assert p.match_pending is False


def test_upsert_recrawl_does_not_clobber_tracking(store: StateStore) -> None:
    _seed(store)
    store.set_track_status("acme", "https://acme.test/a", "applied", stamp_applied=True)
    store.set_notes("acme", "https://acme.test/a", "referred by dana")
    store.set_dismissed("acme", "https://acme.test/a", True)
    # A later crawl re-sees the same posting (even with drifted fields).
    store.upsert_position(
        "acme",
        "https://acme.test/a",
        fields={**FIELDS, "salary_min": 200000},
        content_hash="h2",
    )
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None
    assert p.track_status == "applied"  # survived the re-crawl
    assert p.applied_at is not None
    assert p.notes == "referred by dana"
    assert p.dismissed is True
    assert p.salary_min == 200000  # crawler-owned field still refreshed


# -- notes -------------------------------------------------------------------


def test_set_and_clear_notes(store: StateStore) -> None:
    _seed(store)
    store.set_notes("acme", "https://acme.test/a", "phone screen tuesday")
    assert _get(store).notes == "phone screen tuesday"
    for empty in ("", None):
        store.set_notes("acme", "https://acme.test/a", empty)
        assert _get(store).notes is None


# -- dismiss -----------------------------------------------------------------


def test_dismiss_is_not_a_close(store: StateStore) -> None:
    _seed(store)
    store.set_dismissed("acme", "https://acme.test/a", True)
    p = store.get_position("acme", "https://acme.test/a")
    assert p is not None
    assert p.dismissed is True
    assert p.status == "open"  # a soft UI hide, not a crawler close
    store.set_dismissed("acme", "https://acme.test/a", False)
    assert _get(store).dismissed is False


# -- search_positions --------------------------------------------------------


def test_search_all_and_query(store: StateStore) -> None:
    _seed(store, url="https://acme.test/a", title="Rust Platform Engineer")
    _seed(store, url="https://acme.test/b", title="Frontend Engineer")
    assert len(store.search_positions()) == 2
    hits = store.search_positions("rust")
    assert [p.url for p in hits] == ["https://acme.test/a"]
    # query also matches company/location/description
    assert len(store.search_positions("acme")) == 2


def test_search_filters(store: StateStore) -> None:
    _seed(
        store,
        site="acme",
        url="https://acme.test/a",
        workplace="remote",
        level="senior",
        salary_max=210000,
    )
    _seed(store, site="beta", url="https://beta.test/b", workplace="onsite")
    store.set_match(
        "acme", "https://acme.test/a", score=92, verdict="strong", reasons="[]"
    )

    assert [p.site for p in store.search_positions(site="acme")] == ["acme"]
    assert [p.url for p in store.search_positions(workplace="remote")] == [
        "https://acme.test/a"
    ]
    assert [p.url for p in store.search_positions(level="SENIOR")] == [
        "https://acme.test/a"
    ]  # case-insensitive
    assert [p.url for p in store.search_positions(min_score=90)] == [
        "https://acme.test/a"
    ]
    assert store.search_positions(min_score=95) == []
    assert [p.url for p in store.search_positions(min_salary=200000)] == [
        "https://acme.test/a"
    ]


def test_search_track_status_and_lifecycle(store: StateStore) -> None:
    _seed(store, url="https://acme.test/a")
    _seed(store, url="https://acme.test/b")
    store.set_track_status("acme", "https://acme.test/a", "applied")
    assert [p.url for p in store.search_positions(track_status="applied")] == [
        "https://acme.test/a"
    ]

    # search reads closed rows too (you still track a role after the board closes it)
    store.mark_closed("acme", "https://acme.test/b", reason="test")
    assert {p.url for p in store.search_positions()} == {
        "https://acme.test/a",
        "https://acme.test/b",
    }
    assert [p.url for p in store.search_positions(status="closed")] == [
        "https://acme.test/b"
    ]
    assert [p.url for p in store.search_positions(status="open")] == [
        "https://acme.test/a"
    ]


def test_search_dismissed_filter(store: StateStore) -> None:
    _seed(store, url="https://acme.test/a")
    _seed(store, url="https://acme.test/b")
    store.set_dismissed("acme", "https://acme.test/a", True)
    assert [p.url for p in store.search_positions(dismissed=False)] == [
        "https://acme.test/b"
    ]
    assert [p.url for p in store.search_positions(dismissed=True)] == [
        "https://acme.test/a"
    ]
    assert len(store.search_positions(dismissed=None)) == 2  # both


def test_search_exclude_company_applied(store: StateStore) -> None:
    # Applying to any role at a company hides that company's *other* openings from browse,
    # grouped on the canonical company key (§16) — the browse-side twin of matches()' flag.
    _seed(store, url="https://acme.test/a", company="Acme")
    _seed(store, url="https://acme.test/b", company="Acme")
    _seed(store, site="globex", url="https://globex.test/c", company="Globex")
    store.set_track_status("acme", "https://acme.test/a", "applied", stamp_applied=True)

    assert len(store.search_positions()) == 3  # off by default: full corpus
    # one applied Acme role hides the whole company; the other company's lead survives
    assert [p.url for p in store.search_positions(exclude_company_applied=True)] == [
        "https://globex.test/c"
    ]


def test_search_sort_and_pagination(store: StateStore) -> None:
    for i, score in enumerate([50, 90, 70]):
        url = f"https://acme.test/{i}"
        _seed(store, url=url, title=f"Role {i}")
        store.set_match("acme", url, score=score, verdict="v", reasons="[]")
    by_score = store.search_positions(sort="score")
    assert [p.match_score for p in by_score] == [90, 70, 50]
    # pagination: limit/offset walk the same ordering
    first = store.search_positions(sort="score", limit=1, offset=0)
    second = store.search_positions(sort="score", limit=1, offset=1)
    assert first[0].match_score == 90 and second[0].match_score == 70


# -- track_counts ------------------------------------------------------------


def test_track_counts(store: StateStore) -> None:
    _seed(store, url="https://acme.test/a")
    _seed(store, url="https://acme.test/b")
    _seed(store, url="https://acme.test/c")
    store.set_track_status("acme", "https://acme.test/a", "applied")
    store.set_track_status("acme", "https://acme.test/b", "applied")
    store.set_dismissed("acme", "https://acme.test/c", True)
    counts = store.track_counts()
    assert counts["applied"] == 2
    assert counts["new"] == 1  # the dismissed one keeps its stage
    assert counts["dismissed"] == 1  # counted separately


# -- crawl request queue -----------------------------------------------------


def test_request_and_take_crawl_requests(store: StateStore) -> None:
    store.request_crawl("acme")
    store.request_crawl("beta")
    store.request_crawl("acme")  # idempotent upsert — collapses to one
    taken = store.take_crawl_requests()
    assert sorted(taken) == ["acme", "beta"]
    # draining is exhaustive: a second take is empty until something is requested again
    assert store.take_crawl_requests() == []


# -- open_counts_by_site -----------------------------------------------------


def test_open_counts_by_site(store: StateStore) -> None:
    _seed(store, site="acme", url="https://acme.test/a")
    _seed(store, site="acme", url="https://acme.test/b")
    _seed(store, site="beta", url="https://beta.test/a")
    store.mark_closed("acme", "https://acme.test/b", reason="test")
    counts = store.open_counts_by_site()
    assert counts["acme"] == 1  # b was closed
    assert counts["beta"] == 1


# -- migration ---------------------------------------------------------------


def test_migration_backfills_tracking_columns(tmp_path: object) -> None:
    # A positions table created before the §16 tracking columns: opening it must ALTER-in
    # the tracking columns with their defaults and leave the legacy row usable.
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        INSERT INTO positions (site, url, content_hash, title, company, location, first_seen, last_seen)
        VALUES ('acme', 'u', 'h', 'T', 'C', 'L', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        cols = {row["name"] for row in s._conn.execute("PRAGMA table_info(positions)")}
        assert {
            "track_status",
            "applied_at",
            "notes",
            "dismissed",
            "track_updated_at",
        } <= cols
        # the legacy row reads back with the tracking defaults and tracking still works
        p = s.get_position("acme", "u")
        assert p is not None
        assert p.track_status == "new" and p.dismissed is False and p.applied_at is None
        s.set_track_status("acme", "u", "applied", stamp_applied=True)
        assert _get(s, "acme", "u").track_status == "applied"
    finally:
        s.close()


def test_crawl_requests_table_created_on_open(tmp_path: object) -> None:
    # A pre-§16 DB without the crawl_requests table must gain it (idempotent _SCHEMA).
    db = f"{tmp_path}/legacy.db"  # type: ignore[str-bytes-safe]
    raw = sqlite3.connect(db)
    raw.executescript(
        """
        CREATE TABLE positions (
            site TEXT NOT NULL, url TEXT NOT NULL, content_hash TEXT NOT NULL,
            title TEXT NOT NULL, company TEXT NOT NULL, location TEXT NOT NULL,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY (site, url)
        );
        """
    )
    raw.commit()
    raw.close()

    s = StateStore(db)
    try:
        s.request_crawl("acme")  # would raise if the table were missing
        assert s.take_crawl_requests() == ["acme"]
    finally:
        s.close()
