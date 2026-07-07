"""The crawl-state store (§4.7): position lifecycle + dedup + runs + match cache.

The jobcrawler owns a small state store decoupled from any external sink. SQLite is
the v1 backend (Postgres is the §13 follow-up). It carries the persistent memory that
makes this a job *tracker* rather than a fire-and-forget ingester:

  * **position lifecycle** (the ``positions`` table) — one row per (site, canonical
    URL). Each crawl ``upsert_position``s every job it yields, and the row remembers
    ``first_seen`` (discovery), ``last_seen`` (every touch), a ``content_hash`` over the
    job's identity fields (an edited posting is ``'changed'`` and gets re-matched; an
    untouched one is ``'unchanged'`` and costs nothing), and a ``missing_streak``. Jobs
    that stop appearing are diffed by ``close_missing`` and, after ``close_after_misses``
    consecutive absences, marked ``closed``; ``expire_due`` closes rows past their stated
    ``expires_at``. This open→closed lifecycle is precisely what a job *board* has and a
    news feed does not. A closed posting that reappears REOPENS with its match verdict
    intact, so a churned-then-returned role never re-bills the matcher.

  * **LLM match cache** (``match_cache`` + ``match_spend``) — the host-side profile
    matcher (§6/§8) is the only LLM surface *outside* the crawl sandbox and it bills
    tokens, so its verdicts are memoized by (content_hash, profile_hash, model,
    prompt_version): an unchanged job under an unchanged profile is scored once, ever
    (cache hits are free). ``match_spend`` is a per-UTC-day scored counter enforcing the
    daily cap — jobs past it are flagged pending on their row (``match_pending``) and
    drained by later runs, never dropped. Together these make matching idempotent and
    budget-bounded.

  * **item state / runs / health / schedule** — the original §4.7 surfaces are retained
    byte-compatible (other subsystems still call them): per-(site,url) ``seen``/
    ``ingested`` dedup that never downgrades an ingested URL, one ``RunRecord`` per crawl
    (items/new/errors/pages + §4.8 drift ``signals``), the §4.8 health row + append-only
    transition log, and the §4.9 learned interval.

This module is persistence only. *When* to upsert, close, or match — and every threshold
that decides it — is the runner/matcher's call, kept host-side (the §8 hidden-scorer
invariant); the store records state and never invents policy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime) -> str:
    """Serialize a ``datetime`` as **UTC** ISO text so lexicographic compares are sound.

    Every timestamp compare in this store is lexicographic on ISO text (``expire_due``'s
    ``expires_at < now``, the health-log window). That is only correct if every value is
    in the *same* zone: a naive datetime and an offset-aware one differ in both suffix
    (``+00:00`` vs none) and wall-clock digits, so a Tokyo ``…T08:00+09:00`` (which is an
    already-past ``…T23:00Z``) would sort *after* a UTC ``now`` and never expire. We
    normalize first: a naive value is assumed UTC (the store's convention), an aware one
    is converted to UTC, then isoformatted — so the compare is on true instants.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _sql_value(value: object) -> object:
    """Coerce a crawler-supplied field value to a SQLite-storable scalar.

    The only non-scalar a ``Job`` carries into ``positions`` is a ``datetime`` (its
    ``posted_at``/``expires_at``), which we store as **UTC** ISO text (:func:`_utc_iso`)
    like every other timestamp in this store — so ``expires_at`` compares lexicographically
    against the clock in :meth:`StateStore.expire_due` on a true instant (a non-UTC or naive
    expiry can't slip past the compare) and reads back through ``datetime.fromisoformat``.
    Everything else (str/int/float/None) passes through untouched.
    """
    if isinstance(value, datetime):
        return _utc_iso(value)
    return value


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


@dataclass(frozen=True, slots=True)
class HealthRecord:
    """A site's current health row: state + when it was set + the attempt counter.

    ``attempts`` counts consecutive generation failures (§4.8 retry-with-backoff):
    it increments each time the site is re-recorded ``generation_failed`` and resets
    to 0 on any other state, so ``updated_at`` + ``attempts`` give the healer the
    backoff schedule for re-trying a failed generation with the current generator.
    """

    state: str
    updated_at: datetime
    attempts: int


@dataclass(frozen=True, slots=True)
class PositionRecord:
    """One row of the ``positions`` table: a tracked job's full lifecycle state (§4.7).

    Mirrors the row so the status/notify/rematch surfaces read a typed record rather
    than a raw ``sqlite3.Row``. ISO-text timestamps are parsed back to aware
    ``datetime`` (``None`` when unset) and the ``match_pending`` flag reads back as a
    ``bool``, matching the ``RunRecord`` precedent. The store owns the lifecycle columns
    (``first_seen``/``last_seen``/``missing_streak``/``status``/``closed_at``), the
    ``match_*`` verdict columns, and the operator-owned tracking axis
    (``track_status``/``applied_at``/``notes``/``dismissed``/``track_updated_at``, §16);
    the remainder are the crawler-supplied job fields threaded verbatim through
    :meth:`StateStore.upsert_position`. The tracking axis is orthogonal to the crawler
    ``status`` — a posting can be board-``closed`` while ``track_status='interviewing'``.
    """

    site: str
    url: str
    external_id: str | None
    apply_url: str | None
    content_hash: str
    title: str
    company: str
    company_canonical: str | None
    hiring_company: str | None
    location: str
    location_normalized: str | None
    description: str | None
    discipline: str | None
    level: str | None
    workplace: str | None
    remote_scope: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    employment_type: str | None
    department: str | None
    posted_at: datetime | None
    expires_at: datetime | None
    first_seen: datetime
    last_seen: datetime
    missing_streak: int
    status: str
    closed_at: datetime | None
    match_score: int | None
    match_verdict: str | None
    match_reasons: str | None
    match_concerns: str | None
    matched_at: datetime | None
    match_pending: bool
    track_status: str
    applied_at: datetime | None
    notes: str | None
    dismissed: bool
    track_updated_at: datetime | None


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
    updated_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS health_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    site       TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS health_log_by_site ON health_log (site, id DESC);
CREATE TABLE IF NOT EXISTS schedule_state (
    site         TEXT PRIMARY KEY,
    interval_secs REAL NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    site            TEXT NOT NULL,
    url             TEXT NOT NULL,
    external_id     TEXT,
    apply_url       TEXT,
    content_hash    TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    company_canonical TEXT,
    hiring_company  TEXT,
    location        TEXT NOT NULL,
    location_normalized TEXT,
    description     TEXT,
    discipline      TEXT,
    level           TEXT,
    workplace       TEXT,
    remote_scope    TEXT,
    salary_min      REAL,
    salary_max      REAL,
    salary_currency TEXT,
    salary_period   TEXT,
    employment_type TEXT,
    department      TEXT,
    posted_at       TEXT,
    expires_at      TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    missing_streak  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',
    closed_at       TEXT,
    match_score     INTEGER,
    match_verdict   TEXT,
    match_reasons   TEXT,
    match_concerns  TEXT,
    matched_at      TEXT,
    match_pending   INTEGER NOT NULL DEFAULT 0,
    track_status     TEXT NOT NULL DEFAULT 'new',
    applied_at       TEXT,
    notes            TEXT,
    dismissed        INTEGER NOT NULL DEFAULT 0,
    track_updated_at TEXT,
    PRIMARY KEY (site, url)
);
CREATE TABLE IF NOT EXISTS match_cache (
    content_hash   TEXT,
    profile_hash   TEXT,
    model          TEXT,
    prompt_version TEXT,
    verdict_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (content_hash, profile_hash, model, prompt_version)
);
CREATE TABLE IF NOT EXISTS match_spend (
    day    TEXT PRIMARY KEY,
    scored INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS extract_cache (
    content_hash    TEXT,
    model           TEXT,
    prompt_version  TEXT,
    extraction_json TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (content_hash, model, prompt_version)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_requests (
    slug         TEXT PRIMARY KEY,
    requested_at TEXT NOT NULL
);
"""

# Columns of ``positions`` the runner supplies per crawl via ``upsert_position(fields=)``.
# Everything else — the lifecycle (first_seen/last_seen/missing_streak/status/closed_at)
# and the ``match_*`` verdict — is owned by the store's own methods and is never taken
# from the caller's dict. Unknown keys are ignored, so a crawler that yields a field the
# schema does not track never breaks the upsert. ``description`` is stored verbatim: the
# row must be a *complete* record of the posting so the §4.7 matcher can rebuild its LLM
# digest from the store alone (a profile edit re-scores the whole backlog without waiting
# for each board's next crawl to re-serve the text).
_POSITION_FIELDS: frozenset[str] = frozenset(
    {
        "external_id",
        "apply_url",
        "title",
        "company",
        "company_canonical",
        "location",
        "location_normalized",
        "description",
        "discipline",
        "level",
        "workplace",
        "remote_scope",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "employment_type",
        "department",
        "posted_at",
        "expires_at",
    }
)

# Additive column backfills for a ``positions`` table created before the lifecycle/match
# columns existed (the §4.7 tracker + §6 matching layers landed on top of a base
# site/url/hash/title/company/location/first_seen/last_seen row). Each is nullable or
# carries a DEFAULT, so ``ALTER TABLE ADD COLUMN`` succeeds on a populated table; guarded
# on ``PRAGMA table_info`` in ``_migrate`` so it is a no-op on a current DB. Mirrors the
# runs.signals / site_health.attempts retrofits.
_POSITION_ADD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("external_id", "TEXT"),
    # The canonical external apply link (§4.6). Distinct from ``url`` for aggregators
    # (RemoteOK/MyWeb3Jobs/WWR store the listing page as ``url`` and the company's own
    # application URL here); many ATS crawlers set it to the hosted apply page. Nullable —
    # rows written before it existed read NULL until their next crawl re-supplies it.
    ("apply_url", "TEXT"),
    # §4.6 canonical display/grouping form of ``location`` (country/region synonyms
    # folded, consistent casing). Derived host-side, kept out of ``content_hash``;
    # nullable so it backfills on next crawl. Display falls back to raw ``location``.
    ("location_normalized", "TEXT"),
    ("description", "TEXT"),
    ("discipline", "TEXT"),
    ("level", "TEXT"),
    ("workplace", "TEXT"),
    ("remote_scope", "TEXT"),
    ("salary_min", "REAL"),
    ("salary_max", "REAL"),
    ("salary_currency", "TEXT"),
    ("salary_period", "TEXT"),
    ("employment_type", "TEXT"),
    ("department", "TEXT"),
    # §16 hiring-company dedup key, derived host-side from ``company`` at upsert. Nullable so
    # it backfills cleanly; rows written before it existed read NULL until their next crawl,
    # and the ``matches`` grouping COALESCEs to ``lower(company)`` so dedup still holds.
    ("company_canonical", "TEXT"),
    # §4.7 match-time LLM-extracted employer name (set only when a description names one and
    # the board didn't); written by ``set_hiring_company``, never by a crawl upsert.
    ("hiring_company", "TEXT"),
    ("posted_at", "TEXT"),
    ("expires_at", "TEXT"),
    ("missing_streak", "INTEGER NOT NULL DEFAULT 0"),
    ("status", "TEXT NOT NULL DEFAULT 'open'"),
    ("closed_at", "TEXT"),
    ("match_score", "INTEGER"),
    ("match_verdict", "TEXT"),
    ("match_reasons", "TEXT"),
    ("match_concerns", "TEXT"),
    ("matched_at", "TEXT"),
    ("match_pending", "INTEGER NOT NULL DEFAULT 0"),
    # The §16 operator tracking axis, landed on top of the match layer. Like ``match_*``
    # these are store-owned and never taken from a crawler's ``fields`` dict, so a re-crawl
    # of an already-tracked posting can never clobber the application state you set.
    ("track_status", "TEXT NOT NULL DEFAULT 'new'"),
    ("applied_at", "TEXT"),
    ("notes", "TEXT"),
    ("dismissed", "INTEGER NOT NULL DEFAULT 0"),
    ("track_updated_at", "TEXT"),
)


class _Result:
    """The drained rows of one query plus the write metadata callers read.

    Exposes the slice of the cursor API the store uses — ``fetchone`` / ``fetchall`` /
    iteration and ``lastrowid`` / ``rowcount`` — over rows already fetched into memory,
    so nothing touches the live connection after :class:`_SerializedConnection` drops
    its lock.
    """

    def __init__(
        self, rows: list[Any], lastrowid: int | None, rowcount: int
    ) -> None:
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self) -> list[Any]:
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Any]:
        while self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            yield row


class _SerializedConnection:
    """Serializes every operation on one sqlite3 connection shared across threads.

    ``sqlite3.connect(check_same_thread=False)`` lets a single connection cross
    threads, but it does **not** make *concurrent* use safe: two threads stepping the
    same connection race and raise ``sqlite3.InterfaceError: bad parameter or other API
    misuse``. The web UI (§16) serves reads from an anyio threadpool while the §4.9
    scheduler writes off-thread, so every call funnels through one reentrant lock.

    Reads are drained under the lock — the cursor's rows are fetched into a
    :class:`_Result` before the lock is released — so callers keep using
    ``fetchone`` / ``fetchall`` / iteration afterwards without touching the live
    connection. Writers that already hold the lock (``with self._lock:`` around
    execute+commit) re-enter it harmlessly, which keeps a statement and its commit
    atomic as one transaction.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, parameters: Any = ()) -> _Result:
        with self._lock:
            cur = self._conn.execute(sql, parameters)
            return _Result(cur.fetchall(), cur.lastrowid, cur.rowcount)

    def executemany(self, sql: str, seq_of_parameters: Any) -> _Result:
        with self._lock:
            cur = self._conn.executemany(sql, seq_of_parameters)
            return _Result([], cur.lastrowid, cur.rowcount)

    def executescript(self, sql: str) -> _Result:
        with self._lock:
            cur = self._conn.executescript(sql)
            return _Result([], cur.lastrowid, cur.rowcount)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str) -> Any:
        # Delegate anything we don't wrap (e.g. row_factory reads) to the raw conn.
        return getattr(self._conn, name)


class StateStore:
    def __init__(
        self, path: str = ":memory:", *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._clock = clock
        self._lock = threading.RLock()  # serializes the shared connection (§16 + §4.9)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        self._conn = _SerializedConnection(conn, self._lock)
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
        health_cols = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(site_health)")
        }
        if "attempts" not in health_cols:
            self._conn.execute(
                "ALTER TABLE site_health ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
            )
        # ``_SCHEMA`` already ran (executescript), so ``positions`` exists with its full
        # column set on a fresh DB; on a DB that predates the lifecycle/match columns the
        # CREATE IF NOT EXISTS left the old table alone and these ALTERs backfill the gap.
        position_cols = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(positions)")
        }
        if (
            position_cols
        ):  # guard the empty-table_info case (table absent) belt-and-braces
            for name, ddl in _POSITION_ADD_COLUMNS:
                if name not in position_cols:
                    self._conn.execute(f"ALTER TABLE positions ADD COLUMN {name} {ddl}")
        # Built here rather than in _SCHEMA because it references status/match_score, which
        # the loop above only just backfilled onto a pre-lifecycle DB. The §16 dashboard
        # filters open+scored rows and orders by score (top matches, the score histogram,
        # the "matched" count); without this index each was a full scan+sort of the whole
        # corpus. status leads for the equality prefix, match_score DESC matches the ORDER
        # BY, and both columns being covered makes the histogram index-only.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS positions_status_score "
            "ON positions (status, match_score DESC)"
        )
        self._backfill_remote_scope()

    def _backfill_remote_scope(self) -> None:
        """Fill ``remote_scope`` for rows written before the column existed (§4.6).

        The scope derives from ``workplace``+``location`` via the very classifier a fresh
        crawl runs (:func:`classify_remote_scope`), so a populated DB gains the geographic
        scope immediately instead of waiting for every board's next crawl to re-serve the
        row. Idempotent and cheap: it only reads rows still NULL with a known ``workplace``,
        and the classifier never returns NULL — so once filled, a later open matches nothing
        and skips. (Rows with a NULL ``workplace`` stay NULL, which the filters/display read
        as ``unspecified`` anyway.)
        """
        rows = self._conn.execute(
            "SELECT site, url, workplace, location FROM positions "
            "WHERE remote_scope IS NULL AND workplace IS NOT NULL"
        ).fetchall()
        if not rows:
            return
        from jobcrawler.runtime.normalize import classify_remote_scope

        self._conn.executemany(
            "UPDATE positions SET remote_scope = ? WHERE site = ? AND url = ?",
            [
                (
                    classify_remote_scope(row["workplace"] or "", row["location"] or ""),
                    row["site"],
                    row["url"],
                )
                for row in rows
            ],
        )

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

    def get_health_record(self, site: str) -> HealthRecord | None:
        """``site``'s full health row (state + updated_at + attempts), or None.

        Used by the §4.8 retry-with-backoff: ``updated_at`` is when the current
        state was last set and ``attempts`` is the consecutive generation-failure
        count, which together fix when a failed generation may be re-tried.
        """
        row = self._conn.execute(
            "SELECT state, updated_at, attempts FROM site_health WHERE site = ?",
            (site,),
        ).fetchone()
        if row is None:
            return None
        return HealthRecord(
            state=row["state"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            attempts=row["attempts"],
        )

    def set_health(self, site: str, state: str, *, bump_attempts: bool = False) -> None:
        """Upsert ``site``'s health state, logging a transition on real change.

        ``site_health`` keeps only the latest state (overwritten each call); the
        append-only ``health_log`` gains a row only when the state actually changes
        (prior != new), so the §4.8 drift lifecycle is recorded with measured
        timestamps. A no-op re-assert of the same state writes no log row.

        ``bump_attempts`` increments the consecutive-failure counter (the caller sets
        it when recording another ``generation_failed``); otherwise the counter resets
        to 0. The §4.8 retry-with-backoff reads it back via :meth:`get_health_record`.
        """
        now = self._clock().isoformat()
        with self._lock:
            prior = self._conn.execute(
                "SELECT state, attempts FROM site_health WHERE site = ?", (site,)
            ).fetchone()
            prior_state = prior["state"] if prior is not None else None
            prior_attempts = prior["attempts"] if prior is not None else 0
            attempts = prior_attempts + 1 if bump_attempts else 0
            if prior_state != state:
                self._conn.execute(
                    "INSERT INTO health_log (site, from_state, to_state, ts) "
                    "VALUES (?, ?, ?, ?)",
                    (site, prior_state, state, now),
                )
            self._conn.execute(
                "INSERT INTO site_health (site, state, updated_at, attempts) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (site) DO UPDATE SET state = ?, updated_at = ?, "
                "attempts = ?",
                (site, state, now, attempts, state, now, attempts),
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

    def count_transitions_to(
        self, state: str, *, since: datetime, until: datetime | None = None
    ) -> int:
        """Count health_log rows that entered ``state`` in a window, across *all* sites.

        ``since`` is inclusive, ``until`` (if given) exclusive. Unlike
        :meth:`transitions_for` this is fleet-wide, not per-site: the §4.11
        post-adoption regression watcher uses it to compare how many sites newly
        entered ``generation_failed`` in the window after a generator adoption against
        the equal window before it (an adopted generator that regresses produces more
        new failures). Timestamps are stored as UTC isoformat, which sorts
        chronologically as text, so the bounds compare correctly without parsing.
        """
        if until is not None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM health_log "
                "WHERE to_state = ? AND ts >= ? AND ts < ?",
                (state, since.isoformat(), until.isoformat()),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM health_log WHERE to_state = ? AND ts >= ?",
                (state, since.isoformat()),
            ).fetchone()
        return int(row["n"])

    # -- adaptive cadence (§4.9 hit-rate controller) -------------------------

    def get_interval(self, site: str) -> float | None:
        """The persisted learned crawl interval (seconds) for ``site``, or None.

        None means the controller has not yet recorded one (a freshly-added adaptive
        site), so the scheduler seeds from a default. Persistence only: the control
        law lives in :mod:`jobcrawler.adaptive`.
        """
        row = self._conn.execute(
            "SELECT interval_secs FROM schedule_state WHERE site = ?", (site,)
        ).fetchone()
        return row["interval_secs"] if row is not None else None

    def set_interval(self, site: str, secs: float) -> None:
        """Upsert ``site``'s learned crawl interval (seconds)."""
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO schedule_state (site, interval_secs, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (site) DO UPDATE SET interval_secs = ?, updated_at = ?",
                (site, secs, now, secs, now),
            )
            self._conn.commit()

    # -- position lifecycle (§4.7 tracker) -----------------------------------

    def upsert_position(
        self, site: str, url: str, *, fields: dict[str, object], content_hash: str
    ) -> str:
        """Record a job seen this crawl; return ``'new'`` / ``'changed'`` / ``'unchanged'``.

        The dedup key is ``content_hash`` (the runner hashes the job's identity fields —
        title/company/location/description). Semantics the runner and matcher depend on:

          * **no prior row** → insert it (``first_seen``/``last_seen`` = now) and return
            ``'new'``.
          * **prior row** → always touch ``last_seen`` and reset ``missing_streak`` to 0
            (the job is present again, so any pending closure is cancelled), and refresh
            the crawler-supplied fields (salary/expiry/etc. can drift without changing the
            identity hash). Return ``'changed'`` iff the ``content_hash`` differs.
          * **prior row was closed** → REOPEN it (``status='open'``, ``closed_at=NULL``)
            and return ``'changed'`` so the reappearance is logged and re-surfaced.

        The ``match_*`` columns are never touched here — the verdict *stays* across a
        reopen or an edit. Combined with the (content_hash-keyed) match cache, a reopened
        or unchanged job re-scores from cache for free and never re-bills the LLM; a truly
        edited job (new hash) is collected as ``'changed'`` and re-scored, its new content
        a cache miss. Unknown ``fields`` keys are ignored (see ``_POSITION_FIELDS``).
        """
        now = self._clock().isoformat()
        cols = {k: _sql_value(v) for k, v in fields.items() if k in _POSITION_FIELDS}
        with self._lock:
            prior = self._conn.execute(
                "SELECT content_hash, status FROM positions WHERE site = ? AND url = ?",
                (site, url),
            ).fetchone()
            if prior is None:
                values: dict[str, object] = {
                    "site": site,
                    "url": url,
                    "content_hash": content_hash,
                    "first_seen": now,
                    "last_seen": now,
                }
                values.update(cols)
                names = list(values)
                self._conn.execute(
                    f"INSERT INTO positions ({', '.join(names)}) "
                    f"VALUES ({', '.join('?' for _ in names)})",
                    tuple(values[n] for n in names),
                )
                self._conn.commit()
                return "new"

            reopening = prior["status"] == "closed"
            changed = reopening or prior["content_hash"] != content_hash
            assignments = [f"{k} = ?" for k in cols]
            params: list[object] = list(cols.values())
            assignments += ["content_hash = ?", "last_seen = ?", "missing_streak = 0"]
            params += [content_hash, now]
            if reopening:
                assignments += ["status = 'open'", "closed_at = NULL"]
            params += [site, url]
            self._conn.execute(
                f"UPDATE positions SET {', '.join(assignments)} "
                "WHERE site = ? AND url = ?",
                tuple(params),
            )
            self._conn.commit()
            return "changed" if changed else "unchanged"

    def close_missing(
        self, site: str, present: set[str], *, threshold: int
    ) -> list[str]:
        """Age out ``site``'s open jobs absent from ``present``; return the newly closed.

        ``present`` is every URL *seen alive* this crawl — the tracked (upserted) positions
        PLUS any served-but-incomplete re-serve the completeness floor skipped (a
        momentarily-truncated description must not age a live posting toward closure). For
        every OPEN row of ``site`` whose URL is not in ``present``, increment
        ``missing_streak``; a row that reaches ``threshold`` consecutive misses flips to
        ``status='closed'`` with ``closed_at=now`` and is returned. A row that IS in
        ``present`` is alive, so its streak is reset to 0: ``upsert_position`` already did
        so for a tracked row, and this additionally clears a streak accrued by a row that
        was only served incomplete this time — otherwise a truncated re-serve could silently
        push a live posting toward closure. The streak gives a job a grace window (a
        transient enumeration hiccup doesn't instantly close a live posting); the §4.7
        mass-closure guard (only run this after a non-empty, error-free, enumerating crawl)
        protects against a broken crawl closing the whole board at once.
        """
        now = self._clock().isoformat()
        closed: list[str] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT url, missing_streak FROM positions "
                "WHERE site = ? AND status = 'open'",
                (site,),
            ).fetchall()
            for row in rows:
                if row["url"] in present:
                    # Seen alive this crawl. A tracked row was already reset by
                    # upsert_position; a served-but-incomplete re-serve was not, so clear
                    # any accrued streak here — a truncated posting is still a live one.
                    if row["missing_streak"]:
                        self._conn.execute(
                            "UPDATE positions SET missing_streak = 0 "
                            "WHERE site = ? AND url = ?",
                            (site, row["url"]),
                        )
                    continue
                streak = row["missing_streak"] + 1
                if streak >= threshold:
                    self._conn.execute(
                        "UPDATE positions SET missing_streak = ?, status = 'closed', "
                        "closed_at = ? WHERE site = ? AND url = ?",
                        (streak, now, site, row["url"]),
                    )
                    closed.append(row["url"])
                else:
                    self._conn.execute(
                        "UPDATE positions SET missing_streak = ? "
                        "WHERE site = ? AND url = ?",
                        (streak, site, row["url"]),
                    )
            self._conn.commit()
        return closed

    def mark_closed(self, site: str, url: str, *, reason: str) -> None:
        """Force-close one open position now (e.g. an expired-marker hit, an operator call).

        Idempotent: only an OPEN row is closed (``closed_at`` is stamped once and not
        re-written on a re-close). ``reason`` documents the caller's intent for the log/
        notify line; the pinned §4.7 schema keeps no close-reason column, so it is not
        persisted — closure is a single ``status``/``closed_at`` transition.
        """
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET status = 'closed', closed_at = ? "
                "WHERE site = ? AND url = ? AND status = 'open'",
                (now, site, url),
            )
            self._conn.commit()

    def expire_due(self) -> int:
        """Close every open position whose ``expires_at`` is now past; return the count.

        ``expires_at`` is ISO text and compared lexicographically against the clock, the
        same UTC-isoformat-sorts-chronologically trick the health-log window uses — so the
        runner must store an aware/UTC ``expires_at`` for the compare to be sound. NULL
        expiries never match (the vast majority of postings state none).
        """
        now = self._clock().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE positions SET status = 'closed', closed_at = ? "
                "WHERE status = 'open' AND expires_at IS NOT NULL AND expires_at < ?",
                (now, now),
            )
            self._conn.commit()
            return cur.rowcount

    def open_positions(
        self, site: str | None = None, *, limit: int = 500
    ) -> list[PositionRecord]:
        """Open positions, newest-discovered first — one site or the whole fleet."""
        if site is None:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE status = 'open' "
                "ORDER BY first_seen DESC, site, url LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM positions WHERE site = ? AND status = 'open' "
                "ORDER BY first_seen DESC, url LIMIT ?",
                (site, limit),
            ).fetchall()
        return [_to_position(r) for r in rows]

    def open_urls(self, site: str, *, limit: int) -> list[str]:
        """Open URLs for ``site``, stalest ``last_seen`` first (§4.9 recheck order).

        Ordered least-recently-seen first so the recheck loop's cap spends its budget on
        the positions most likely to have quietly closed — the ones that have fallen out
        of a windowed source's (``enumerates_all=False``) recent view, where the
        closure-diff is unsound and this per-URL re-verification stands in for it.
        """
        rows = self._conn.execute(
            "SELECT url FROM positions WHERE site = ? AND status = 'open' "
            "ORDER BY last_seen ASC, url LIMIT ?",
            (site, limit),
        ).fetchall()
        return [row["url"] for row in rows]

    def pending_urls(self, site: str, *, limit: int) -> list[str]:
        """Open URLs for ``site`` still awaiting scoring (``match_pending = 1``), FIFO.

        The runner's per-site drain seam (§4.7): a job parked over the daily/first-crawl
        cap is a cache *miss* (never LLM-scored), so a plain re-crawl that re-yields it as
        ``'unchanged'`` would never re-present it and it would starve. On a non-first crawl
        the runner intersects this set with the positions re-seen this pass and hands the
        overlap to :meth:`Matcher.score_new`, which finally scores them within budget —
        the "drained by later runs" contract. Oldest-discovered first so the stalest
        backlog drains first (mirrors :meth:`pending_matches`' FIFO order, scoped to one
        site so a concurrent crawl fetches only its own backlog).
        """
        rows = self._conn.execute(
            "SELECT url FROM positions WHERE site = ? AND status = 'open' "
            "AND match_pending = 1 ORDER BY first_seen ASC, url LIMIT ?",
            (site, limit),
        ).fetchall()
        return [row["url"] for row in rows]

    def matches(
        self,
        min_score: int,
        *,
        limit: int = 50,
        exclude_applied: bool = False,
        exclude_dismissed: bool = False,
        exclude_company_applied: bool = False,
    ) -> list[PositionRecord]:
        """Open, scored positions at or above ``min_score``, best fit first (§6).

        ``exclude_applied`` drops rows you've already applied to — ``applied_at`` is
        stamped for the §16 applied/interviewing/offer/accepted stages — so the
        dashboard's "top matches" surfaces only leads still worth pursuing rather than
        ones already moving through your pipeline. ``exclude_company_applied`` widens that
        to the whole hiring *company*: once you've applied to any position there, its other
        openings drop out too — most companies accept one application company-wide, so a
        second lead at a company you're already in is noise. Companies are grouped on the
        derived ``company_canonical`` key (COALESCE'd to ``lower(company)`` for rows not yet
        re-crawled), so 'Acme' and 'Acme, Inc.' count as one and an empty key groups with
        nothing. ``exclude_dismissed`` additionally drops rows you've hidden with the §16
        dismiss button. All are off by default: the CLI listing and the score histogram
        want the full scored corpus.
        """
        clauses = ""
        if exclude_applied:
            clauses += "AND applied_at IS NULL "
        if exclude_company_applied:
            clauses += (
                "AND NOT EXISTS (SELECT 1 FROM positions p2 "
                "WHERE COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) "
                "= COALESCE(NULLIF(positions.company_canonical, ''), lower(positions.company)) "
                "AND COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) != '' "
                "AND p2.applied_at IS NOT NULL) "
            )
        if exclude_dismissed:
            clauses += "AND COALESCE(dismissed, 0) = 0 "
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' AND match_score IS NOT NULL "
            "AND match_score >= ? "
            + clauses
            + "ORDER BY match_score DESC, matched_at DESC, url LIMIT ?",
            (min_score, limit),
        ).fetchall()
        return [_to_position(r) for r in rows]

    def score_histogram(self, bands: Collection[tuple[int, int]]) -> list[int]:
        """Count open, scored positions falling in each inclusive ``[lo, hi]`` band.

        The dashboard score histogram (§16) used to materialize up to 5000 full
        ``PositionRecord`` objects — every column, including the multi-KB
        ``description`` — just to read one integer each, which dominated page-load time
        on a real corpus. This buckets in a single SQL aggregate over the
        ``(status, match_score)`` index (index-only), returning one count per band.

        Bands are matched in order, so overlapping ranges resolve to the first that
        contains the score; a score outside every band is not counted. Returns a list of
        counts aligned to ``bands``.
        """
        band_list = list(bands)
        if not band_list:
            return []
        # lo/hi are trusted ints from the caller's band table (not user input), so the
        # inline CASE is safe — mirrors the store's other f-string-composed SQL.
        cases = " ".join(
            f"WHEN match_score BETWEEN {int(lo)} AND {int(hi)} THEN {i}"
            for i, (lo, hi) in enumerate(band_list)
        )
        rows = self._conn.execute(
            f"SELECT CASE {cases} END AS band, COUNT(*) AS n "
            "FROM positions WHERE status = 'open' AND match_score IS NOT NULL "
            "GROUP BY band"
        ).fetchall()
        counts = [0] * len(band_list)
        for row in rows:
            band = row["band"]
            if band is not None:
                counts[band] = row["n"]
        return counts

    def set_match(
        self,
        site: str,
        url: str,
        *,
        score: int,
        verdict: str,
        reasons: str,
        concerns: str = "[]",
    ) -> None:
        """Record a match verdict on a position and clear its pending flag (§6).

        ``matched_at`` is stamped from the clock. Applied both for a fresh LLM verdict and
        for a free cache hit — either way the row now carries the score and is no longer
        pending. ``reasons``/``concerns`` are opaque text to the store (the matcher
        serializes its rationale/concerns lists into them); persisting both keeps the full
        verdict on the durable row so a match trainset can be projected from it (§8).
        """
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET match_score = ?, match_verdict = ?, "
                "match_reasons = ?, match_concerns = ?, matched_at = ?, match_pending = 0 "
                "WHERE site = ? AND url = ?",
                (score, verdict, reasons, concerns, now, site, url),
            )
            self._conn.commit()

    def set_salary(
        self,
        site: str,
        url: str,
        *,
        salary_min: float | None,
        salary_max: float | None,
        salary_currency: str | None,
        salary_period: str | None,
    ) -> bool:
        """Backfill LLM-extracted compensation, but only onto a row that has NONE (§4.7).

        Guarded to ``salary_min IS NULL AND salary_max IS NULL`` so a source-structured
        salary — a crawler field or the §4.6 host parse — always wins and is never
        clobbered by an LLM guess. Salary is not part of ``content_hash``, so writing it
        never re-dedups a posting or invalidates its match verdict. Returns whether a row
        was actually filled (False = it already had a salary, or no such row).
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE positions SET salary_min = ?, salary_max = ?, "
                "salary_currency = ?, salary_period = ? "
                "WHERE site = ? AND url = ? "
                "AND salary_min IS NULL AND salary_max IS NULL",
                (salary_min, salary_max, salary_currency, salary_period, site, url),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def set_hiring_company(
        self, site: str, url: str, *, name: str, canonical: str
    ) -> None:
        """Record the §4.7 LLM-extracted employer, and point the §16 dedup key at it.

        The board's own ``company``/``company_canonical`` stay as crawled; this adds the
        cleaner employer a description named — a Lever board with no company field, or a
        "Confidential" listing whose text says the real name — and, when that yields a
        non-empty canonical, repoints ``company_canonical`` at it so company dedup groups on
        the actual employer. An empty canonical never clobbers the crawl-derived key.
        Idempotent: re-applying the cached extraction each match run restores the key after a
        crawl upsert reverts it to ``canonical_company(company)``. Neither is in
        ``content_hash``.
        """
        with self._lock:
            if canonical:
                self._conn.execute(
                    "UPDATE positions SET hiring_company = ?, company_canonical = ? "
                    "WHERE site = ? AND url = ?",
                    (name, canonical, site, url),
                )
            else:
                self._conn.execute(
                    "UPDATE positions SET hiring_company = ? WHERE site = ? AND url = ?",
                    (name, site, url),
                )
            self._conn.commit()

    def set_match_pending(self, site: str, url: str) -> None:
        """Flag a position as awaiting scoring — over the daily cap, or a match failure.

        Deliberately does not touch ``match_score``: a job past the cap is *deferred*, not
        rejected, and drained by :meth:`pending_matches` on a later run (fail-open — the
        matcher never drops a job or raises into the crawl loop).
        """
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET match_pending = 1 WHERE site = ? AND url = ?",
                (site, url),
            )
            self._conn.commit()

    def clear_match_pending(self, site: str, url: str) -> None:
        """Unpark a position without recording a verdict (deterministic prefilter miss).

        The store-side drain rebuilds candidates from parked rows; one the prefilter now
        definitely rejects must leave the FIFO (or it would clog every future drain
        window) but has earned no LLM verdict — it returns to plain "tracked, unscored",
        the same state the runner gives a fresh prefilter miss. A later profile widening
        re-presents it via :meth:`requeue_open_matches`.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET match_pending = 0 WHERE site = ? AND url = ?",
                (site, url),
            )
            self._conn.commit()

    def requeue_open_matches(self) -> int:
        """Park every open position for (re)scoring; return how many rows changed.

        The §4.7 profile-edit path: a new ``profile_hash`` invalidates every cached
        verdict, so ALL open rows — scored under the old profile, prefilter-skipped, or
        never presented — go back through the matcher. Unchanged (job, profile) pairs
        re-apply from cache for free; only genuinely new pairs reach the LLM, still under
        the daily cap. Closed rows stay untouched (history keeps its old verdicts).
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE positions SET match_pending = 1 WHERE status = 'open'"
            )
            self._conn.commit()
            return cur.rowcount

    def pending_matches(
        self, *, limit: int, with_description_only: bool = False
    ) -> list[PositionRecord]:
        """Open positions awaiting scoring, oldest-discovered first (FIFO backlog drain).

        ``with_description_only`` narrows the window to rows the matcher can rebuild *from
        the store* — those with a stored ``description``. The LLM rebuild-drain
        (:func:`jobcrawler.match.drain_candidates`) sets it: a description-less row rebuilds
        to nothing (``candidate_from_row`` yields ``None``), so if it were selected it would
        be counted unscorable and left parked — sitting at the FIFO head and re-consuming
        the ``limit`` window every tick while newer scorable rows starve behind it. Such a
        row can only regain a description via a re-crawl (which drains it through the
        per-site :meth:`pending_urls` seam), so the rebuild-drain excludes it rather than
        let it wedge the window; it stays ``match_pending = 1`` until re-crawled or closed.

        The default (``False``) returns every parked row, because the free cache-drain
        (``rematch`` pass 1) scores by ``content_hash`` alone and never touches the
        description — a description-less row with a cached verdict is legitimately scorable
        there, so excluding it would forfeit a free hit.
        """
        clause = (
            " AND description IS NOT NULL AND description != ''"
            if with_description_only
            else ""
        )
        rows = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' AND match_pending = 1"
            f"{clause} ORDER BY first_seen ASC, site, url LIMIT ?",
            (limit,),
        ).fetchall()
        return [_to_position(r) for r in rows]

    # -- operator tracking axis (§16 web UI) -----------------------------------
    #
    # The personal application pipeline, orthogonal to the crawler-owned open/closed
    # lifecycle above. These are thin persisted-string writers: the *valid set*, the
    # transition policy, and which stages stamp ``applied_at`` are host-side policy
    # (``jobcrawler.tracking``, read by the web layer) — the store never adjudicates a
    # value, mirroring how ``set_match`` is threshold-agnostic (§4.7). None of these touch
    # the crawler ``status``/``closed_at`` or the ``match_*`` columns.

    def get_position(self, site: str, url: str) -> PositionRecord | None:
        """One position by identity ``(site, url)``, any lifecycle state, or None."""
        row = self._conn.execute(
            "SELECT * FROM positions WHERE site = ? AND url = ?", (site, url)
        ).fetchone()
        return _to_position(row) if row is not None else None

    def set_track_status(
        self, site: str, url: str, status: str, *, stamp_applied: bool = False
    ) -> None:
        """Set the operator's pipeline stage on a position (§16); stamp ``track_updated_at``.

        ``status`` is persisted as an opaque string (the caller validates it host-side via
        ``tracking.parse`` — the store adjudicates neither the value nor the transition, so
        a personal search can move backwards or skip stages freely). When ``stamp_applied``
        is set — the caller saw an application-implying stage
        (``applied``/``interviewing``/``offer``/``accepted``) — ``applied_at`` is stamped
        too, but only if not already set (``COALESCE``), so the original apply date survives
        a later stage change or a move back down the funnel. The crawler ``status`` and the
        ``match_*`` verdict are never touched (the axes are orthogonal, §4.7).
        """
        now = self._clock().isoformat()
        with self._lock:
            if stamp_applied:
                self._conn.execute(
                    "UPDATE positions SET track_status = ?, track_updated_at = ?, "
                    "applied_at = COALESCE(applied_at, ?) WHERE site = ? AND url = ?",
                    (status, now, now, site, url),
                )
            else:
                self._conn.execute(
                    "UPDATE positions SET track_status = ?, track_updated_at = ? "
                    "WHERE site = ? AND url = ?",
                    (status, now, site, url),
                )
            self._conn.commit()

    def set_notes(self, site: str, url: str, notes: str | None) -> None:
        """Overwrite the free-text notes on a position (§16); empty/``None`` clears them.

        Stamps ``track_updated_at`` (a notes edit is an operator touch). Orthogonal to the
        crawler lifecycle and the ``match_*`` columns.
        """
        text = notes if notes else None
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET notes = ?, track_updated_at = ? "
                "WHERE site = ? AND url = ?",
                (text, now, site, url),
            )
            self._conn.commit()

    def set_dismissed(self, site: str, url: str, dismissed: bool) -> None:
        """Toggle the hide flag on a position (§16) — a soft UI hide, NOT a crawler close.

        A dismissed row stays ``status='open'`` on the board (``close_missing`` still owns
        that axis); the UI simply filters it out by default. Stamps ``track_updated_at``.
        """
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE positions SET dismissed = ?, track_updated_at = ? "
                "WHERE site = ? AND url = ?",
                (1 if dismissed else 0, now, site, url),
            )
            self._conn.commit()

    def search_positions(
        self,
        query: str = "",
        *,
        site: str | None = None,
        company: str | None = None,
        company_canonical: str | None = None,
        status: str | None = None,
        track_status: str | None = None,
        workplace: str | None = None,
        remote_scope: str | None = None,
        level: str | None = None,
        min_score: int | None = None,
        min_salary: float | None = None,
        dismissed: bool | None = None,
        exclude_company_applied: bool = False,
        sort: str = "recent",
        limit: int = 100,
        offset: int = 0,
    ) -> list[PositionRecord]:
        """The web UI's browse/filter reader (§16) — positions across *any* lifecycle state.

        Unlike :meth:`open_positions`/:meth:`matches`, this reads open *and* closed rows so
        the operator can still find a role they applied to after the board closed it. Every
        filter is optional and ANDed; all are host-supplied (no crawler/generator input):

        - ``query``   case-insensitive substring over title/company/location (raw +
          normalized)/description.
        - ``site``    exact slug; ``company`` case-insensitive substring;
          ``company_canonical`` exact dedup key (the "positions at this company" link).
        - ``status``  crawler lifecycle ``'open'``/``'closed'`` (``None``/``'all'`` = both).
        - ``track_status``  exact personal stage (``'applied'`` …).
        - ``workplace``/``level``  exact, case-insensitive (e.g. ``'remote'``).
        - ``remote_scope``  exact geographic scope of a remote role (``'anywhere'`` powers the
          "remote anywhere" filter; ``'restricted'``/``'unspecified'`` also selectable).
        - ``min_score``  only rows scored at/above it (drops unscored).
        - ``min_salary``  ``COALESCE(salary_max, salary_min) >= min_salary`` (drops unstated).
        - ``dismissed``  ``None`` = both, ``False`` = hide dismissed, ``True`` = only dismissed.
        - ``exclude_company_applied``  drop rows whose hiring company already has an applied-to
          posting (grouped on ``company_canonical``, §16) — the browse twin of :meth:`matches`.

        ``sort`` ∈ {recent, score, updated, salary, title}; NULL scores/salaries sink last
        under a DESC sort. ``limit``/``offset`` paginate. Returns ``list[PositionRecord]``.
        """
        clauses: list[str] = []
        params: list[object] = []
        q = query.strip()
        if q:
            like = f"%{q.lower()}%"
            clauses.append(
                "(lower(title) LIKE ? OR lower(company) LIKE ? "
                "OR lower(location) LIKE ? OR lower(COALESCE(location_normalized, '')) LIKE ? "
                "OR lower(COALESCE(description, '')) LIKE ?)"
            )
            params += [like, like, like, like, like]
        if site:
            clauses.append("site = ?")
            params.append(site)
        if company:
            clauses.append("lower(company) LIKE ?")
            params.append(f"%{company.lower()}%")
        if company_canonical:
            # Exact match on the derived dedup key (§16) — the "all positions from this
            # company" link. Collapses surface variants ("Acme, Inc."/"ACME") and, when
            # the matcher set it, aggregator postings onto the real hiring company.
            clauses.append("company_canonical = ?")
            params.append(company_canonical)
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        if track_status:
            clauses.append("track_status = ?")
            params.append(track_status)
        if workplace:
            clauses.append("lower(COALESCE(workplace, '')) = ?")
            params.append(workplace.lower())
        if remote_scope:
            clauses.append("lower(COALESCE(remote_scope, '')) = ?")
            params.append(remote_scope.lower())
        if level:
            clauses.append("lower(COALESCE(level, '')) = ?")
            params.append(level.lower())
        if min_score is not None:
            clauses.append("match_score IS NOT NULL AND match_score >= ?")
            params.append(min_score)
        if min_salary is not None:
            clauses.append("COALESCE(salary_max, salary_min) >= ?")
            params.append(min_salary)
        if dismissed is not None:
            clauses.append("dismissed = ?")
            params.append(1 if dismissed else 0)
        if exclude_company_applied:
            # Browse-side twin of matches()' flag: hide every row whose hiring company
            # already has an applied-to posting, grouped on the derived company_canonical
            # key (COALESCE'd to lower(company)); an empty key groups with nothing (§16).
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM positions p2 "
                "WHERE COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) "
                "= COALESCE(NULLIF(positions.company_canonical, ''), lower(positions.company)) "
                "AND COALESCE(NULLIF(p2.company_canonical, ''), lower(p2.company)) != '' "
                "AND p2.applied_at IS NOT NULL)"
            )
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = {
            "recent": "first_seen DESC, site, url",
            "score": "match_score DESC, matched_at DESC, url",
            "updated": "track_updated_at DESC, first_seen DESC, url",
            "salary": "COALESCE(salary_max, salary_min) DESC, url",
            "title": "lower(title) ASC, url",
        }.get(sort, "first_seen DESC, site, url")
        rows = self._conn.execute(
            f"SELECT * FROM positions{where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [_to_position(r) for r in rows]

    def track_counts(self) -> dict[str, int]:
        """Tallies for the personal tracking axis (§16): one entry per *present*
        ``track_status`` plus ``dismissed`` (counted separately — a dismissed row keeps its
        stage). Stages with no rows are absent (the store does not know the enum — that is
        host policy in ``tracking``; the web layer zero-fills). Orthogonal to
        :meth:`position_counts` (the crawler lifecycle).
        """
        rows = self._conn.execute(
            "SELECT track_status, COUNT(*) AS n FROM positions GROUP BY track_status"
        ).fetchall()
        out: dict[str, int] = {row["track_status"]: int(row["n"]) for row in rows}
        dm = self._conn.execute(
            "SELECT COUNT(*) AS n FROM positions WHERE dismissed = 1"
        ).fetchone()
        out["dismissed"] = int(dm["n"])
        return out

    # -- manual crawl requests (§16 crawl-now button) --------------------------
    #
    # A tiny queue the web UI writes and the §4.9 scheduler drains: the UI cannot run a
    # second scheduler (that would violate the single-owner "a board never overlaps its own
    # run" rule), so "crawl now" just flags the slug here and the daemon folds it into its
    # next due set. Requesting a slug the daemon does not know is a silent no-op there.

    def request_crawl(self, slug: str) -> None:
        """Queue a manual crawl of ``slug`` for the scheduler to pick up (§16).

        Idempotent upsert keyed by slug — a second request before the daemon drains
        collapses to one pending request. Does nothing itself; the daemon owns the crawl.
        """
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO crawl_requests (slug, requested_at) VALUES (?, ?) "
                "ON CONFLICT (slug) DO UPDATE SET requested_at = excluded.requested_at",
                (slug, now),
            )
            self._conn.commit()

    def take_crawl_requests(self) -> list[str]:
        """Atomically drain and return all queued manual-crawl slugs (§16).

        The §4.9 scheduler calls this each tick and unions the slugs into its due set.
        Read-then-delete under the write lock (single connection) is atomic, so a slug is
        never handed out twice or lost to a concurrent :meth:`request_crawl`. Oldest
        request first, though order is immaterial (the daemon dedups against ``_inflight``).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT slug FROM crawl_requests ORDER BY requested_at, slug"
            ).fetchall()
            self._conn.execute("DELETE FROM crawl_requests")
            self._conn.commit()
            return [row["slug"] for row in rows]

    # -- meta (small persisted key/value scalars) ------------------------------

    def get_meta(self, key: str) -> str | None:
        """The persisted scalar for ``key``, or None if never set."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Persist a small scalar the daemon must remember across restarts.

        First use: ``profile_hash`` — the hash verdicts were last applied under, so a
        ``PROFILE.md`` edited while the daemon is DOWN is still detected on the next
        boot's drain tick and the backlog is requeued (§4.7).
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def position_counts(self) -> dict[str, int]:
        """Coarse lifecycle tallies for status surfaces.

        ``matched`` counts open positions that carry a verdict (scored, any verdict incl.
        reject); ``pending`` counts open positions still awaiting scoring. The two are
        disjoint subsets of ``open`` (a scored row has ``match_pending=0``); an open row
        can be in neither yet (newly discovered, not yet sent to the matcher). Callers
        wanting only *good* matches filter with :meth:`matches`.
        """

        def _count(where: str) -> int:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM positions WHERE {where}"
            ).fetchone()
            return int(row["n"])

        return {
            "open": _count("status = 'open'"),
            "closed": _count("status = 'closed'"),
            "matched": _count("status = 'open' AND match_score IS NOT NULL"),
            "pending": _count("status = 'open' AND match_pending = 1"),
        }

    def open_counts_by_site(self) -> dict[str, int]:
        """Open-position count per site slug — the §16 dashboard's per-board tally."""
        rows = self._conn.execute(
            "SELECT site, COUNT(*) AS n FROM positions WHERE status = 'open' "
            "GROUP BY site"
        ).fetchall()
        return {row["site"]: int(row["n"]) for row in rows}

    # -- LLM match cache & daily spend (§6/§8) --------------------------------

    def cached_match(
        self, content_hash: str, profile_hash: str, model: str, prompt_version: str
    ) -> str | None:
        """The cached verdict JSON for an exact (hash, profile, model, version) key, or None.

        A hit means this job's content, under this profile, was already scored by this
        model+prompt — so re-scoring it is free (no tokens). Any dimension differing (an
        edited posting → new content_hash, an edited PROFILE.md → new profile_hash, a
        model swap, a ``PROMPT_VERSION`` bump) misses and forces a fresh score.
        """
        row = self._conn.execute(
            "SELECT verdict_json FROM match_cache WHERE content_hash = ? "
            "AND profile_hash = ? AND model = ? AND prompt_version = ?",
            (content_hash, profile_hash, model, prompt_version),
        ).fetchone()
        return row["verdict_json"] if row is not None else None

    def cache_match(
        self,
        content_hash: str,
        profile_hash: str,
        model: str,
        prompt_version: str,
        verdict_json: str,
    ) -> None:
        """Memoize a fresh LLM verdict under its full cache key (upsert on the key)."""
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO match_cache (content_hash, profile_hash, model, "
                "prompt_version, verdict_json, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (content_hash, profile_hash, model, prompt_version) "
                "DO UPDATE SET verdict_json = ?, created_at = ?",
                (
                    content_hash,
                    profile_hash,
                    model,
                    prompt_version,
                    verdict_json,
                    now,
                    verdict_json,
                    now,
                ),
            )
            self._conn.commit()

    def cached_extract(
        self, content_hash: str, model: str, prompt_version: str
    ) -> str | None:
        """The cached description-extraction JSON for a job's content, or None (§4.7).

        Unlike :meth:`cached_match` this key omits ``profile_hash``: salary and employer are
        properties of the *posting*, not of whose profile is matching, so one extraction
        serves every profile and every re-score for free.
        """
        row = self._conn.execute(
            "SELECT extraction_json FROM extract_cache WHERE content_hash = ? "
            "AND model = ? AND prompt_version = ?",
            (content_hash, model, prompt_version),
        ).fetchone()
        return row["extraction_json"] if row is not None else None

    def cache_extract(
        self, content_hash: str, model: str, prompt_version: str, extraction_json: str
    ) -> None:
        """Memoize a fresh description extraction under its (content, model, version) key."""
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO extract_cache (content_hash, model, prompt_version, "
                "extraction_json, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (content_hash, model, prompt_version) "
                "DO UPDATE SET extraction_json = ?, created_at = ?",
                (
                    content_hash,
                    model,
                    prompt_version,
                    extraction_json,
                    now,
                    extraction_json,
                    now,
                ),
            )
            self._conn.commit()

    def match_spent(self, day: str) -> int:
        """How many jobs have been LLM-scored on ``day`` (UTC ``YYYY-MM-DD``); 0 if none.

        Only cache *misses* (real token spend) are counted by the matcher via
        :meth:`bump_match_spend`; free cache hits are not, so the daily cap bounds spend,
        not throughput.
        """
        row = self._conn.execute(
            "SELECT scored FROM match_spend WHERE day = ?", (day,)
        ).fetchone()
        return int(row["scored"]) if row is not None else 0

    def bump_match_spend(self, day: str, n: int) -> None:
        """Add ``n`` scored jobs to ``day``'s counter (creating the day row on first use)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO match_spend (day, scored) VALUES (?, ?) "
                "ON CONFLICT (day) DO UPDATE SET scored = scored + ?",
                (day, n, n),
            )
            self._conn.commit()

    def reserve_match_spend(
        self, day: str, n: int, *, cap: int, reserved: int = 0
    ) -> int:
        """Atomically claim up to ``n`` of ``day``'s remaining budget; return the grant.

        The daily cap's concurrency-safe primitive (§4.7/§8). Reads the day's current spend
        and increments it **under the same lock**, so the read-decide-write is one critical
        section — a shared :class:`~jobcrawler.match.Matcher` fanned out across concurrent
        ``asyncio.to_thread`` crawls can no longer each read a stale spend and collectively
        overshoot ``cap`` by up to the concurrency (the TOCTOU the plain
        ``match_spent`` + late ``bump_match_spend`` pair allowed). Returns
        ``max(0, min(n, cap - reserved - current))``; the caller scores exactly that many
        and parks the overflow pending. The grant is charged **up front** (before the LLM
        calls), so the count reflects jobs *sent*, not just successfully scored — a failed
        batch still burned tokens, and never under-counting is what keeps the shared cap
        sound.

        ``reserved`` holds that many of the cap untouched for another caller (§4.7 drain
        floor): the fresh crawl path passes the drain floor so a saturating day of new
        postings still leaves the backlog drain a guaranteed slice, while the drain passes
        ``0`` and so may spend the floor plus anything fresh scoring left unused.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT scored FROM match_spend WHERE day = ?", (day,)
            ).fetchone()
            current = int(row["scored"]) if row is not None else 0
            granted = max(0, min(n, cap - reserved - current))
            if granted:
                self._conn.execute(
                    "INSERT INTO match_spend (day, scored) VALUES (?, ?) "
                    "ON CONFLICT (day) DO UPDATE SET scored = scored + ?",
                    (day, granted, granted),
                )
                self._conn.commit()
            return granted

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


def _to_position(row: sqlite3.Row) -> PositionRecord:
    def _dt(name: str) -> datetime | None:
        raw = row[name]
        return datetime.fromisoformat(raw) if raw else None

    return PositionRecord(
        site=row["site"],
        url=row["url"],
        external_id=row["external_id"],
        apply_url=row["apply_url"],
        content_hash=row["content_hash"],
        title=row["title"],
        company=row["company"],
        company_canonical=row["company_canonical"],
        hiring_company=row["hiring_company"],
        location=row["location"],
        location_normalized=row["location_normalized"],
        description=row["description"],
        discipline=row["discipline"],
        level=row["level"],
        workplace=row["workplace"],
        remote_scope=row["remote_scope"],
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
        employment_type=row["employment_type"],
        department=row["department"],
        posted_at=_dt("posted_at"),
        expires_at=_dt("expires_at"),
        first_seen=datetime.fromisoformat(row["first_seen"]),
        last_seen=datetime.fromisoformat(row["last_seen"]),
        missing_streak=row["missing_streak"],
        status=row["status"],
        closed_at=_dt("closed_at"),
        match_score=row["match_score"],
        match_verdict=row["match_verdict"],
        match_reasons=row["match_reasons"],
        match_concerns=row["match_concerns"],
        matched_at=_dt("matched_at"),
        match_pending=bool(row["match_pending"]),
        track_status=row["track_status"],
        applied_at=_dt("applied_at"),
        notes=row["notes"],
        dismissed=bool(row["dismissed"]),
        track_updated_at=_dt("track_updated_at"),
    )
