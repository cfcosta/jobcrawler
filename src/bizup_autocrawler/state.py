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

import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """A single crawl's health counters (§5 canonical Run record).

    ``signals`` carries the optional §4.8 partial-drift telemetry threaded from
    the runner (e.g. ``body_quality``, ``incomplete``) — a small JSON blob so new
    signals can be added without a schema migration. It is host-side observability
    only and is never exposed to the generator/RLM (the §8 hidden-scorer invariant).
    """

    id: int
    site: str
    started: datetime
    finished: datetime | None
    items_found: int
    new_items: int
    errors: int
    pages_fetched: int
    signals: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class HealthTransition:
    """One row of the append-only health-transition log (§4.8 observability).

    ``set_health`` appends a row only when the state actually changes, so a real
    drift lifecycle (HEALTHY → STALE → … → HEALTHY) is recorded with measured
    timestamps. This is what lets ``metrics.drift_mttr`` read true recovery times
    rather than reconstructing them from run history. ``from_state`` is None for a
    site's first recorded state.
    """

    site: str
    from_state: str | None
    to_state: str
    ts: datetime


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
    pages_fetched INTEGER NOT NULL DEFAULT 0,
    signals       TEXT
);
CREATE INDEX IF NOT EXISTS runs_by_site ON runs (site, id DESC);
CREATE TABLE IF NOT EXISTS site_health (
    site       TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    site       TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS health_log_by_site ON health_log (site, id DESC);
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
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for DBs created before a column existed.

        ``_SCHEMA`` covers fresh DBs; this backfills columns onto pre-existing
        ones. Idempotent (guarded on ``PRAGMA table_info``) so it is safe to run
        on every open.
        """
        run_cols = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")
        }
        if "signals" not in run_cols:
            self._conn.execute("ALTER TABLE runs ADD COLUMN signals TEXT")

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
        signals: dict[str, object] | None = None,
    ) -> None:
        payload = json.dumps(signals) if signals is not None else None
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished = ?, items_found = ?, new_items = ?, "
                "errors = ?, pages_fetched = ?, signals = ? WHERE id = ?",
                (
                    self._clock().isoformat(),
                    items_found,
                    new_items,
                    errors,
                    pages_fetched,
                    payload,
                    run_id,
                ),
            )
            self._conn.commit()

    def reap_orphans(self) -> int:
        """Clear runs left unfinished by a crash; returns how many were reaped.

        ``start_run`` inserts ``finished=NULL``; a ``kill -9`` mid-crawl leaves it
        NULL forever, and the scheduler's due-check treats an unfinished last run as
        "in-flight" and never re-dispatches that site — wedging it permanently. Run
        once at daemon startup (single-process §4.9, so no genuinely in-flight run
        exists yet): the orphan row carries no useful counters, so dropping it makes
        the site fall back to its prior completed run (or none) and become due again.
        """
        with self._lock:
            cur = self._conn.execute("DELETE FROM runs WHERE finished IS NULL")
            self._conn.commit()
            return cur.rowcount

    def last_run(self, site: str) -> RunRecord | None:
        history = self.runs_for(site, limit=1)
        return history[0] if history else None

    def runs_for(self, site: str, *, limit: int = 50) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE site = ? ORDER BY id DESC LIMIT ?",
            (site, limit),
        ).fetchall()
        return [_to_record(r) for r in rows]

    # -- per-site health (§4.8) ----------------------------------------------

    def get_health(self, site: str) -> str | None:
        """The persisted health state for ``site`` (a ``HealthState`` value), or None.

        Persistence only: the string is owned by §4.8's ``HealthState`` enum; the
        store deliberately does not import it (the runner/healer translate), so the
        ``health`` module can keep depending on this one.
        """
        row = self._conn.execute(
            "SELECT state FROM site_health WHERE site = ?", (site,)
        ).fetchone()
        return row["state"] if row is not None else None

    def set_health(self, site: str, state: str) -> None:
        """Upsert ``site``'s health state, logging a transition on real change.

        ``site_health`` keeps only the latest state (overwritten each call); the
        append-only ``health_log`` gains a row only when the state actually changes
        (prior != new), so the §4.8 drift lifecycle is recorded with measured
        timestamps. A no-op re-assert of the same state writes no log row.
        """
        now = self._clock().isoformat()
        with self._lock:
            prior = self._conn.execute(
                "SELECT state FROM site_health WHERE site = ?", (site,)
            ).fetchone()
            prior_state = prior["state"] if prior is not None else None
            if prior_state != state:
                self._conn.execute(
                    "INSERT INTO health_log (site, from_state, to_state, ts) "
                    "VALUES (?, ?, ?, ?)",
                    (site, prior_state, state, now),
                )
            self._conn.execute(
                "INSERT INTO site_health (site, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (site) DO UPDATE SET state = ?, updated_at = ?",
                (site, state, now, state, now),
            )
            self._conn.commit()

    def transitions_for(
        self, site: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[HealthTransition]:
        """Health transitions for ``site``, newest-first (the ``*_for`` convention).

        ``since`` filters to transitions at or after a wall-clock time (used by the
        post-adoption regression watcher). Callers needing a chronological timeline
        (e.g. ``metrics.transition_events``) reverse the result.
        """
        if since is not None:
            rows = self._conn.execute(
                "SELECT * FROM health_log WHERE site = ? AND ts >= ? "
                "ORDER BY id DESC LIMIT ?",
                (site, since.isoformat(), limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM health_log WHERE site = ? ORDER BY id DESC LIMIT ?",
                (site, limit),
            ).fetchall()
        return [_to_transition(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


def _to_record(row: sqlite3.Row) -> RunRecord:
    finished = row["finished"]
    raw_signals = row["signals"] if "signals" in row.keys() else None
    return RunRecord(
        id=row["id"],
        site=row["site"],
        started=datetime.fromisoformat(row["started"]),
        finished=datetime.fromisoformat(finished) if finished else None,
        items_found=row["items_found"],
        new_items=row["new_items"],
        errors=row["errors"],
        pages_fetched=row["pages_fetched"],
        signals=json.loads(raw_signals) if raw_signals else None,
    )


def _to_transition(row: sqlite3.Row) -> HealthTransition:
    return HealthTransition(
        site=row["site"],
        from_state=row["from_state"],
        to_state=row["to_state"],
        ts=datetime.fromisoformat(row["ts"]),
    )
