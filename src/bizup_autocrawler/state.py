"""The crawl-state store (§4.7): seen/ingested dedup + run history.

The autocrawler owns a small state store decoupled from the news DB. SQLite is
the v1 backend (Postgres is the §13 follow-up). It holds two things:

  * **item state** — per (site, canonical URL): ``seen`` is set once the runner
    has handled the URL (so it is not re-extracted), ``ingested`` is set only
    after a 2xx from ``POST /news``. The seen-vs-ingested gap is the at-most-once
    mitigation. mark_seen never downgrades an already-ingested URL.
  * **run history** — one row per crawl with the §5 canonical Run record fields
    (items_found / new_items / errors / pages_fetched), read by §4.8 health and
    §4.9 scheduling.

This module is persistence only. *When* to mark seen vs ingested (the §4.7
failure-class re-attempt policy) is the runner's call, not the store's.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """A single crawl's health counters (§5 canonical Run record)."""

    id: int
    site: str
    started: datetime
    finished: datetime | None
    items_found: int
    new_items: int
    errors: int
    pages_fetched: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    site        TEXT NOT NULL,
    url         TEXT NOT NULL,
    state       TEXT NOT NULL,
    first_seen  TEXT NOT NULL,
    ingested_at TEXT,
    PRIMARY KEY (site, url)
);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site          TEXT NOT NULL,
    started       TEXT NOT NULL,
    finished      TEXT,
    items_found   INTEGER NOT NULL DEFAULT 0,
    new_items     INTEGER NOT NULL DEFAULT 0,
    errors        INTEGER NOT NULL DEFAULT 0,
    pages_fetched INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_by_site ON runs (site, id DESC);
"""


class StateStore:
    def __init__(
        self, path: str = ":memory:", *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()  # the §4.9 scheduler may write off-thread
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- item state (dedup) --------------------------------------------------

    def is_seen(self, site: str, url: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM items WHERE site = ? AND url = ?", (site, url)
        ).fetchone()
        return row is not None

    def is_ingested(self, site: str, url: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM items WHERE site = ? AND url = ? AND state = 'ingested'",
            (site, url),
        ).fetchone()
        return row is not None

    def mark_seen(self, site: str, url: str) -> None:
        """Record the URL as handled. Never downgrades an ingested row."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO items (site, url, state, first_seen) VALUES (?, ?, 'seen', ?) "
                "ON CONFLICT (site, url) DO NOTHING",
                (site, url, self._clock().isoformat()),
            )
            self._conn.commit()

    def mark_ingested(self, site: str, url: str) -> None:
        """Record a 2xx ingest; upserts (implies seen) and sets ingested_at."""
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO items (site, url, state, first_seen, ingested_at) "
                "VALUES (?, ?, 'ingested', ?, ?) "
                "ON CONFLICT (site, url) DO UPDATE SET state = 'ingested', ingested_at = ?",
                (site, url, now, now, now),
            )
            self._conn.commit()

    # -- run history ---------------------------------------------------------

    def start_run(self, site: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (site, started) VALUES (?, ?)",
                (site, self._clock().isoformat()),
            )
            self._conn.commit()
            run_id = cur.lastrowid
        assert run_id is not None
        return run_id

    def finish_run(
        self,
        run_id: int,
        *,
        items_found: int,
        new_items: int,
        errors: int,
        pages_fetched: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished = ?, items_found = ?, new_items = ?, "
                "errors = ?, pages_fetched = ? WHERE id = ?",
                (
                    self._clock().isoformat(),
                    items_found,
                    new_items,
                    errors,
                    pages_fetched,
                    run_id,
                ),
            )
            self._conn.commit()

    def last_run(self, site: str) -> RunRecord | None:
        history = self.runs_for(site, limit=1)
        return history[0] if history else None

    def runs_for(self, site: str, *, limit: int = 50) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE site = ? ORDER BY id DESC LIMIT ?",
            (site, limit),
        ).fetchall()
        return [_to_record(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


def _to_record(row: sqlite3.Row) -> RunRecord:
    finished = row["finished"]
    return RunRecord(
        id=row["id"],
        site=row["site"],
        started=datetime.fromisoformat(row["started"]),
        finished=datetime.fromisoformat(finished) if finished else None,
        items_found=row["items_found"],
        new_items=row["new_items"],
        errors=row["errors"],
        pages_fetched=row["pages_fetched"],
    )
