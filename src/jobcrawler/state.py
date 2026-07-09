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
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    relocation: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    salary_usd_min: float | None
    salary_usd_max: float | None
    # §4.6 salary provenance: the verbatim printed compensation text (crawler-owned,
    # re-parseable offline) and where the structured figures came from
    # ('structured' | 'text' | 'llm'; NULL = pre-provenance row).
    salary_raw: str | None
    salary_source: str | None
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
    # §4.12 structured locations: the crawler's raw multi-location array (JSON text),
    # the store-derived canonical display summary, and the extract-pass answer stamp.
    # All three read None on queries that predate the columns' SELECT list.
    locations_json: str | None = None
    location_display: str | None = None
    loc_llm_at: datetime | None = None
    # §17 verdict provenance: 'llm' | 'cache' | 'cluster:<site>\n<url>' | None (legacy
    # rows). Inherited verdicts are excluded from trainsets and reversible in bulk.
    match_origin: str | None = None
    # Not a stored column: the §4.7 freshness-decayed score (base fit × a recency multiplier),
    # computed in SQL by the display/rank queries. None when the query didn't select it.
    effective_score: float | None = None


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
    relocation      TEXT,
    salary_min      REAL,
    salary_max      REAL,
    salary_currency TEXT,
    salary_period   TEXT,
    salary_usd_min  REAL,
    salary_usd_max  REAL,
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
    match_origin    TEXT,
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
CREATE TABLE IF NOT EXISTS fx_rates (
    currency    TEXT PRIMARY KEY,
    rate_to_usd REAL NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    site       TEXT NOT NULL,
    ts         TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    reason     TEXT,
    score      REAL,
    llm_calls  INTEGER NOT NULL DEFAULT 0,
    llm_log    TEXT
);
CREATE INDEX IF NOT EXISTS generation_log_by_site ON generation_log (site, id DESC);
-- §17 read-derived duplicate clustering. position_identity_keys and position_clusters
-- are DISPOSABLE PROJECTIONS: the reconciliation sweep rebuilds them from positions
-- alone; DROP + resweep is lossless. Nothing durable may key on cluster_id.
CREATE TABLE IF NOT EXISTS position_identity_keys (
    site TEXT NOT NULL,
    url  TEXT NOT NULL,
    tier INTEGER NOT NULL,
    key  TEXT NOT NULL,
    PRIMARY KEY (site, url, tier, key)
);
CREATE INDEX IF NOT EXISTS pik_by_key ON position_identity_keys (key);
CREATE TABLE IF NOT EXISTS position_clusters (
    site         TEXT NOT NULL,
    url          TEXT NOT NULL,
    cluster_id   TEXT NOT NULL,
    is_canonical INTEGER NOT NULL DEFAULT 0,
    tier         INTEGER NOT NULL,
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (site, url)
);
CREATE INDEX IF NOT EXISTS pc_by_cluster ON position_clusters (cluster_id);
-- §17 role groups: same-company/same-title postings differing only by location,
-- bundled for the web layer's "N locations" picker. A disposable projection like
-- the two above — but PRESENTATION-ONLY: members are real distinct postings
-- (applied to separately), so matching/notify/lifecycle must never read it.
CREATE TABLE IF NOT EXISTS position_role_groups (
    site        TEXT NOT NULL,
    url         TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (site, url)
);
CREATE INDEX IF NOT EXISTS prg_by_group ON position_role_groups (group_id);
-- §17 notification ledger: append-only per-row FACTS (never derived), so cluster
-- rebuilds can neither lose nor double notification history.
CREATE TABLE IF NOT EXISTS notify_ledger (
    event   TEXT NOT NULL,
    site    TEXT NOT NULL,
    url     TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (event, site, url)
);
-- §17 operator escape hatch: a blocked key forms no edges; blocking splits its
-- group back to singletons at the next sweep (the safe failure direction).
CREATE TABLE IF NOT EXISTS identity_key_blocklist (
    key        TEXT PRIMARY KEY,
    reason     TEXT,
    created_at TEXT NOT NULL
);
-- §4.12 structured locations: the value-keyed parse ledger. One row per EXACT raw
-- location string as served by one site (a positions.location value or one element
-- of positions.locations_json); keyed (site, value) so an LLM fill for an ambiguous
-- site-specific format never leaks to another board's identical string.
-- parse_source='rules' rows are DISPOSABLE (re-derived on any GEO_PARSER_VERSION
-- bump); parse_source='llm' rows are DURABLE facts paid once via the §4.7 extract
-- pass and reused by every posting that ever carries the same string. Immune to
-- crawl-upsert clobber by construction — no crawl path writes here.
CREATE TABLE IF NOT EXISTS location_values (
    site           TEXT NOT NULL,
    value          TEXT NOT NULL,
    places_json    TEXT NOT NULL DEFAULT '[]',
    fence_json     TEXT NOT NULL DEFAULT '[]',
    remote_marker  INTEGER NOT NULL DEFAULT 0,
    worldwide      INTEGER NOT NULL DEFAULT 0,
    parse_quality  TEXT NOT NULL,
    parse_source   TEXT NOT NULL DEFAULT 'rules',
    parser_version INTEGER NOT NULL,
    llm_model      TEXT,
    parsed_at      TEXT NOT NULL,
    PRIMARY KEY (site, value)
);
-- §4.12 per-position resolved places: a presentation/filter overlay in the §17
-- style — source 'rules' and 'llm_value' rows are a disposable projection of
-- positions × location_values (the value tells provenance: parser vs paid ledger
-- fill), rebuilt at any time; source='llm_posting' rows are POSTING-LOCAL facts
-- (description-derived fences) that survive resweeps and purge when the posting's
-- content changes.
-- kind: 'place' | 'remote_fence' | 'remote_anywhere'; country: ISO 3166-1 alpha-2
-- UPPER (NULL when unresolved); macro_region: a geo.MACRO_KEYS key; display: the
-- canonical human form with full English country names ("São Paulo, Brazil",
-- "Remote (United States)"). Matching, notify, lifecycle, and dedup never read it.
CREATE TABLE IF NOT EXISTS position_locations (
    site         TEXT NOT NULL,
    url          TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    city         TEXT,
    subdivision  TEXT,
    country      TEXT,
    macro_region TEXT,
    display      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'rules',
    confidence   TEXT NOT NULL DEFAULT 'high',
    raw_fragment TEXT,
    PRIMARY KEY (site, url, seq)
);
CREATE INDEX IF NOT EXISTS pl_by_country ON position_locations (country, kind);
-- §4.12 macro-region membership (geo.GEO_REGION_MEMBERS materialized for SQL):
-- powers macro-expanded remote_in= filters. Reseeded whole (DELETE + INSERT)
-- whenever GEO_REGION_SEED_VERSION moves — approximate recall data, never
-- eligibility truth.
CREATE TABLE IF NOT EXISTS geo_region_members (
    region  TEXT NOT NULL,
    country TEXT NOT NULL,
    PRIMARY KEY (region, country)
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
        "relocation",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_usd_min",
        "salary_usd_max",
        # §4.6 provenance pair: salary_raw is ALWAYS keyed by the runner (crawler-
        # owned verbatim text, cleared when the board stops printing one);
        # salary_source rides inside the omit-when-absent salary block ('structured'
        # | 'text') and set_salary stamps 'llm' on the backfill path.
        "salary_raw",
        "salary_source",
        "employment_type",
        "department",
        "posted_at",
        "expires_at",
        # §4.12 crawler-supplied raw multi-location array (JSON list of verbatim
        # strings, e.g. Greenhouse offices[] / Lever allLocations). Crawler-owned
        # like `location` itself — always refreshed by the crawl; the DERIVED
        # location surfaces (location_display, position_locations) are store-owned
        # and deliberately absent from this set.
        "locations_json",
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
    ("relocation", "TEXT"),
    ("salary_min", "REAL"),
    ("salary_max", "REAL"),
    ("salary_currency", "TEXT"),
    ("salary_period", "TEXT"),
    # §4.6 annualized-USD band derived from ``salary_*`` — the coherent cross-currency
    # sort/filter key. Nullable; backfilled from existing salaries by ``_backfill_salary_usd``
    # and refreshed on every crawl. Kept out of ``content_hash`` (salary never is).
    ("salary_usd_min", "REAL"),
    ("salary_usd_max", "REAL"),
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
    # §17 verdict provenance: NULL = pre-§17; 'llm' | 'cache' = scored normally;
    # 'cluster:<site>\n<url>' = inherited from a cluster sibling without billing.
    # Store-owned (NOT in _POSITION_FIELDS) so crawls never clobber it; out of
    # content_hash like every derived column, so it never re-bills or re-dedups.
    ("match_origin", "TEXT"),
    # The §16 operator tracking axis, landed on top of the match layer. Like ``match_*``
    # these are store-owned and never taken from a crawler's ``fields`` dict, so a re-crawl
    # of an already-tracked posting can never clobber the application state you set.
    ("track_status", "TEXT NOT NULL DEFAULT 'new'"),
    ("applied_at", "TEXT"),
    ("notes", "TEXT"),
    ("dismissed", "INTEGER NOT NULL DEFAULT 0"),
    ("track_updated_at", "TEXT"),
    # §4.12 crawler-supplied raw multi-location array (in _POSITION_FIELDS, refreshed
    # every crawl). Nullable; rows from boards whose crawler predates the field read
    # NULL and parse from the flat `location` string alone.
    ("locations_json", "TEXT"),
    # §4.12 canonical display summary derived from the parsed places — store-owned
    # (NOT in _POSITION_FIELDS: the hiring_company pattern), kept out of content_hash,
    # NULL falls back to location_normalized/location in every renderer.
    ("location_display", "TEXT"),
    # §4.12 stamp: the §4.7 extract pass answered a location question for this
    # posting (EVEN an empty answer), so the marker-arm gate never re-selects it.
    # Store-owned; reset when the posting's content changes.
    ("loc_llm_at", "TEXT"),
    # §4.6 verbatim printed compensation text, crawler-owned and always refreshed —
    # the offline re-parse source a grammar fix heals stored rows from. Out of
    # content_hash like every derived/side surface.
    ("salary_raw", "TEXT"),
    # §4.6 provenance of the structured salary figures: 'structured' (source data) |
    # 'text' (host parse of the printed string) | 'llm' (§4.7 extract backfill);
    # NULL = row predates provenance.
    ("salary_source", "TEXT"),
)


class _Result:
    """The drained rows of one query plus the write metadata callers read.

    Exposes the slice of the cursor API the store uses — ``fetchone`` / ``fetchall`` /
    iteration and ``lastrowid`` / ``rowcount`` — over rows already fetched into memory,
    so nothing touches the live connection after :class:`_SerializedConnection` drops
    its lock.
    """

    def __init__(self, rows: list[Any], lastrowid: int | None, rowcount: int) -> None:
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


def _live_key_clause(tiers: Collection[int]) -> str:
    """SQL filter selecting §17 identity keys that are *edge-safe without sweep guards*.

    The live sibling gates (verdict donor, notify gate) join ``position_identity_keys``
    directly — between sweeps, no per-group guard has run — so they may only trust key
    namespaces whose equality alone is identity: T0 url, T1 ATS ids, T3 family-scoped
    external ids (the family name is inside the key string), T6 posting ids, and the
    ``applyats:`` half of T2 (per-job ATS hosts). Generic ``apply:`` links and the
    T4/T5 shadow keys need the sweep's corroboration and never pass here. ``tiers``
    values are trusted host-side config ints, never user input (f-string SQL matches
    the store's other trusted-literal composition).
    """
    enabled = {int(t) for t in tiers}
    parts: list[str] = []
    safe = sorted(enabled & {0, 1, 3, 6})
    if safe:
        parts.append(f"k1.tier IN ({', '.join(str(t) for t in safe)})")
    if 2 in enabled:
        parts.append("(k1.tier = 2 AND k1.key LIKE 'applyats:%')")
    if not parts:
        return "0"
    return "(" + " OR ".join(parts) + ")"


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
        # The §16 applied-company exclusion (dashboard "top matches" + the "hide applied
        # companies" browse filter) is a correlated NOT EXISTS over the derived company key,
        # restricted to applied rows. Unindexed that is an O(N²) scan-per-row: on a
        # multi-thousand-row corpus with a handful of applied rows it ran for *minutes*. A
        # PARTIAL EXPRESSION index on the same key expression, limited to the (few)
        # applied_at rows, turns each row's lookup into an index search — minutes → tens of
        # ms. It references company_canonical/applied_at, backfilled just above, so like
        # positions_status_score it is built here rather than in _SCHEMA.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS positions_applied_company_key "
            "ON positions (COALESCE(NULLIF(company_canonical, ''), lower(company))) "
            "WHERE applied_at IS NOT NULL"
        )
        self._backfill_remote_scope()
        self._backfill_salary_usd()
        self._backfill_relocation()
        self._backfill_notify_ledger()
        self._seed_geo_regions()
        self._backfill_locations()

    def _seed_geo_regions(self) -> None:
        """Materialize geo.GEO_REGION_MEMBERS into SQL (§4.12), reseeding on version bumps.

        The whole table is a projection of the in-code dict — DELETE + INSERT keeps it
        byte-faithful (stale members from an older vocabulary can't linger), and the meta
        guard makes every later open a no-op until GEO_REGION_SEED_VERSION moves.
        """
        from jobcrawler.runtime import geo

        if self.get_meta("geo_region_seed_version") == str(geo.GEO_REGION_SEED_VERSION):
            return
        self._conn.execute("DELETE FROM geo_region_members")
        self._conn.executemany(
            "INSERT OR IGNORE INTO geo_region_members (region, country) VALUES (?, ?)",
            [
                (region, country)
                for region, members in geo.GEO_REGION_MEMBERS.items()
                for country in members
            ],
        )
        self.set_meta("geo_region_seed_version", str(geo.GEO_REGION_SEED_VERSION))

    def _backfill_notify_ledger(self, *, force: bool = False) -> None:
        """One-time §17 notify-ledger seed: pre-feature strong matches count as notified.

        Without the seed, a late-arriving board copy of a job whose strong match already
        pinged (before the ledger existed) would ping again — the notify gate would see
        an empty cluster history. Seeds an append-only fact per already-strong row at the
        current ``match_min_score`` threshold; the threshold used is recorded in the meta
        guard value for auditability. Idempotent via the ``notify_ledger_seeded`` meta key
        (and INSERT OR IGNORE, belt-and-braces); ``force`` skips only the meta guard —
        the operator re-seed path (:meth:`seed_notify_ledger`).
        """
        if not force and self.get_meta("notify_ledger_seeded") is not None:
            return
        from jobcrawler import config

        threshold = config.match_min_score()
        self._conn.execute(
            "INSERT OR IGNORE INTO notify_ledger (event, site, url, sent_at) "
            "SELECT 'match', site, url, COALESCE(matched_at, last_seen) FROM positions "
            "WHERE match_score >= ?",
            (threshold,),
        )
        self.set_meta("notify_ledger_seeded", f"threshold={threshold}")

    def seed_notify_ledger(self) -> None:
        """Operator re-seed (``jobcrawl dedup --seed-notify-ledger``): idempotent facts
        at the *current* threshold — useful after lowering ``match_min_score`` or bulk
        imports, instead of waiting for each row's next organic send. Never un-seeds:
        the ledger is append-only, so a raised threshold changes nothing here."""
        self._backfill_notify_ledger(force=True)

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
                    classify_remote_scope(
                        row["workplace"] or "", row["location"] or ""
                    ),
                    row["site"],
                    row["url"],
                )
                for row in rows
            ],
        )

    def _backfill_salary_usd(self) -> None:
        """Fill the annualized-USD band for salaried rows written before the columns existed.

        Derived from ``salary_*`` via the same host-side conversion a fresh crawl runs
        (:func:`usd_annual_band`), so a populated DB gains a coherent cross-currency salary
        sort/filter key immediately instead of waiting for every board's next crawl. Cheap
        and idempotent: it reads only salaried rows still NULL in a *known* currency, so once
        filled a later open matches nothing. Rows in an unrecognized currency/period stay NULL
        (read as unstated by the salary sort/filter), exactly as a fresh crawl would leave them.
        The convertible-currency set is the live :meth:`fx_table` (static majors plus any
        refreshed rates), so a currency the static table couldn't reach becomes fillable the
        first open after rates go live.
        """
        from jobcrawler.runtime.normalize import usd_annual_band

        table = self.fx_table()
        placeholders = ", ".join("?" for _ in table)
        rows = self._conn.execute(
            "SELECT site, url, salary_min, salary_max, salary_currency, salary_period "
            "FROM positions WHERE salary_usd_min IS NULL AND salary_usd_max IS NULL "
            "AND (salary_min IS NOT NULL OR salary_max IS NOT NULL) "
            f"AND upper(COALESCE(salary_currency, '')) IN ({placeholders})",
            tuple(table),
        ).fetchall()
        if not rows:
            return
        updates: list[tuple[float | None, float | None, str, str]] = []
        for row in rows:
            lo, hi = usd_annual_band(
                row["salary_min"],
                row["salary_max"],
                row["salary_currency"],
                row["salary_period"],
                fx_rates=table,
            )
            if lo is None and hi is None:
                continue  # unknown period — leave for a future crawl to resupply
            updates.append((lo, hi, row["site"], row["url"]))
        if updates:
            self._conn.executemany(
                "UPDATE positions SET salary_usd_min = ?, salary_usd_max = ? "
                "WHERE site = ? AND url = ?",
                updates,
            )

    def _backfill_relocation(self) -> None:
        """Fill ``relocation`` for rows written before the column existed (§4.6).

        Derived from ``title``+``description`` via the same host-side classifier a fresh
        crawl runs (:func:`classify_relocation`), so a populated DB gains the relocation
        signal immediately instead of waiting for every board's next crawl. Only rows still
        NULL that carry a stored ``description`` are read (the classifier needs the body);
        the classifier never returns NULL, so once filled a later open matches nothing.
        """
        rows = self._conn.execute(
            "SELECT site, url, title, description FROM positions "
            "WHERE relocation IS NULL AND description IS NOT NULL"
        ).fetchall()
        if not rows:
            return
        from jobcrawler.runtime.normalize import classify_relocation

        self._conn.executemany(
            "UPDATE positions SET relocation = ? WHERE site = ? AND url = ?",
            [
                (
                    classify_relocation(row["title"] or "", row["description"] or ""),
                    row["site"],
                    row["url"],
                )
                for row in rows
            ],
        )

    # -- §4.12 structured locations -------------------------------------------

    def _ledger_entry(self, site: str, value: str) -> dict[str, Any]:
        """Get-or-parse the ``(site, value)`` ledger row (assumes the caller holds the lock).

        Rules rows are disposable: a GEO_PARSER_VERSION mismatch reparses in place. LLM
        rows are durable — but deterministic-first: when a version bump lets the parser
        resolve the string cleanly ('full'/'macro'), the cached LLM reading retires;
        otherwise the row survives with its ``parser_version`` advanced so the string
        is not re-evaluated until the next bump.
        """
        from jobcrawler.runtime import geo

        key = value.strip()
        row = self._conn.execute(
            "SELECT places_json, fence_json, remote_marker, worldwide, parse_quality, "
            "parse_source, parser_version FROM location_values "
            "WHERE site = ? AND value = ?",
            (site, key),
        ).fetchone()
        if row is not None and row["parser_version"] == geo.GEO_PARSER_VERSION:
            return _decode_ledger(row)
        parse = geo.parse_location(key)
        if (
            row is not None
            and row["parse_source"] == "llm"
            and parse.quality not in ("full", "macro")
        ):
            self._conn.execute(
                "UPDATE location_values SET parser_version = ? "
                "WHERE site = ? AND value = ?",
                (geo.GEO_PARSER_VERSION, site, key),
            )
            return _decode_ledger(row)
        entry = _parse_to_entry(parse)
        self._conn.execute(
            "INSERT OR REPLACE INTO location_values (site, value, places_json, "
            "fence_json, remote_marker, worldwide, parse_quality, parse_source, "
            "parser_version, llm_model, parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'rules', ?, NULL, ?)",
            (
                site,
                key,
                json.dumps(entry["places"], ensure_ascii=False),
                json.dumps(entry["fence"], ensure_ascii=False),
                int(entry["remote_marker"]),
                int(entry["worldwide"]),
                entry["quality"],
                geo.GEO_PARSER_VERSION,
                self._clock().isoformat(),
            ),
        )
        return entry

    def location_needs_llm(self, site: str, value: str) -> bool:
        """True when the string's rules parse left it 'partial'/'fail' — the §4.7
        extract pass's ledger-tail arm. Parses (and caches) on first sight."""
        if not value or not value.strip():
            return False
        with self._lock:
            entry = self._ledger_entry(site, value)
            self._conn.commit()
        return entry["source"] == "rules" and entry["quality"] in ("partial", "fail")

    def refresh_position_locations(
        self,
        site: str,
        url: str,
        *,
        location: str,
        locations_json: str | None = None,
        workplace: str | None = None,
        force: bool = False,
        purge_llm: bool = False,
    ) -> None:
        """(Re)build the position's §4.12 projection rows and display summary.

        The runner calls this right after ``upsert_position`` with
        ``force=(outcome != 'unchanged')`` and ``purge_llm=(outcome == 'changed')``:
        posting-local LLM rows (description-derived fences) die with the content
        that produced them, while a plain resweep keeps them. Without ``force`` a
        current-version ledger row proves this position was already projected (every
        row is projected at its own insert/change), so the common unchanged-crawl
        path costs one SELECT. Rows carrying a ``locations_json`` array always
        refresh — the array is not content-hash-guarded.
        """
        from jobcrawler.runtime import geo

        with self._lock:
            if not force and not locations_json and not purge_llm:
                row = self._conn.execute(
                    "SELECT parse_source, parser_version FROM location_values "
                    "WHERE site = ? AND value = ?",
                    (site, (location or "").strip()),
                ).fetchone()
                if row is not None and (
                    row["parse_source"] == "llm"
                    or row["parser_version"] == geo.GEO_PARSER_VERSION
                ):
                    return
            self._refresh_locations_locked(
                site,
                url,
                location=location,
                locations_json=locations_json,
                workplace=workplace,
                purge_llm=purge_llm,
            )
            self._conn.commit()

    def _refresh_locations_locked(
        self,
        site: str,
        url: str,
        *,
        location: str,
        locations_json: str | None,
        workplace: str | None,
        purge_llm: bool,
    ) -> None:
        strings: list[str] = []
        for candidate in [location, *_json_list(locations_json)]:
            text = (candidate or "").strip()
            if text and text not in strings:
                strings.append(text)
        entries = [self._ledger_entry(site, s) for s in strings]
        rows = _compose_projection(entries, workplace)

        kept: list[sqlite3.Row] = []
        if purge_llm:
            self._conn.execute(
                "UPDATE positions SET loc_llm_at = NULL WHERE site = ? AND url = ?",
                (site, url),
            )
        else:
            kept = self._conn.execute(
                "SELECT kind, city, subdivision, country, macro_region, display, "
                "confidence, raw_fragment FROM position_locations "
                "WHERE site = ? AND url = ? AND source = 'llm_posting' ORDER BY seq",
                (site, url),
            ).fetchall()
        self._conn.execute(
            "DELETE FROM position_locations WHERE site = ? AND url = ?", (site, url)
        )
        params: list[tuple[object, ...]] = []
        seq = 0
        for row in rows:
            params.append(
                (site, url, seq, row["kind"], row.get("city"), row.get("subdivision"),
                 row.get("country"), row.get("macro_region"), row["display"],
                 row.get("source", "rules"), row.get("confidence", "high"),
                 row.get("raw_fragment"))
            )
            seq += 1
        for row in kept:
            params.append(
                (site, url, seq, row["kind"], row["city"], row["subdivision"],
                 row["country"], row["macro_region"], row["display"], "llm_posting",
                 row["confidence"], row["raw_fragment"])
            )
            seq += 1
        if params:
            self._conn.executemany(
                "INSERT INTO position_locations (site, url, seq, kind, city, "
                "subdivision, country, macro_region, display, source, confidence, "
                "raw_fragment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params,
            )
        self._recompute_location_display_locked(site, url)

    def _recompute_location_display_locked(self, site: str, url: str) -> None:
        """Derive ``positions.location_display`` from the stored projection rows."""
        from jobcrawler.runtime import geo

        rows = self._conn.execute(
            "SELECT kind, country, macro_region, display FROM position_locations "
            "WHERE site = ? AND url = ? ORDER BY seq",
            (site, url),
        ).fetchall()
        place_items = [r["display"] for r in rows if r["kind"] == "place"]
        fence_names = [
            geo.country_name(r["country"]) or geo.macro_label(r["macro_region"])
            or r["display"]
            for r in rows
            if r["kind"] == "remote_fence"
        ]
        anywhere = any(r["kind"] == "remote_anywhere" for r in rows)
        display = _compose_display(place_items, fence_names, anywhere)
        self._conn.execute(
            "UPDATE positions SET location_display = ? WHERE site = ? AND url = ?",
            (display, site, url),
        )

    def set_location_value_llm(
        self,
        site: str,
        value: str,
        *,
        places: list[dict[str, Any]],
        fence: list[str],
        remote: str,
        model: str,
    ) -> bool:
        """§4.7 ledger fill for a rules-'partial'/'fail' string. First writer wins.

        Returns True when the fill landed; the projections of every position on
        ``site`` carrying the string refresh immediately (later crawls stay
        consistent through the ledger). An empty answer writes nothing — absence
        of geography is recorded per-posting via ``loc_llm_at``, not in the ledger.
        """
        from jobcrawler.runtime import geo

        key = (value or "").strip()
        if not key:
            return False
        if not places and not fence and remote != "worldwide":
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT parse_source, parse_quality FROM location_values "
                "WHERE site = ? AND value = ?",
                (site, key),
            ).fetchone()
            fillable = row is None or (
                row["parse_source"] == "rules"
                and row["parse_quality"] in ("partial", "fail")
            )
            if not fillable:
                return False
            place_dicts = [
                {
                    "city": p.get("city"),
                    "subdivision": p.get("subdivision"),
                    "country": p.get("country"),
                    "macro": p.get("macro"),
                    "confidence": "medium",
                    "raw": "",
                }
                for p in places
            ]
            fence_dicts = [_fence_dict(f) for f in fence]
            has_geo = any(p.get("country") for p in place_dicts) or any(
                f.get("country") for f in fence_dicts
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO location_values (site, value, places_json, "
                "fence_json, remote_marker, worldwide, parse_quality, parse_source, "
                "parser_version, llm_model, parsed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'llm', ?, ?, ?)",
                (
                    site,
                    key,
                    json.dumps(place_dicts, ensure_ascii=False),
                    json.dumps(fence_dicts, ensure_ascii=False),
                    int(remote != "none"),
                    int(remote == "worldwide"),
                    "full" if has_geo else "macro",
                    geo.GEO_PARSER_VERSION,
                    model,
                    self._clock().isoformat(),
                ),
            )
            carriers = self._conn.execute(
                "SELECT url, location, locations_json, workplace FROM positions "
                "WHERE site = ? AND location = ?",
                (site, key),
            ).fetchall()
            for carrier in carriers:
                self._refresh_locations_locked(
                    site,
                    carrier["url"],
                    location=carrier["location"],
                    locations_json=carrier["locations_json"],
                    workplace=carrier["workplace"],
                    purge_llm=False,
                )
            self._conn.commit()
        return True

    def set_position_location_llm(
        self,
        site: str,
        url: str,
        *,
        places: list[dict[str, Any]],
        fence: list[str],
        remote: str,
        model: str,
    ) -> bool:
        """§4.7 marker-arm fill: POSTING-LOCAL rows mined from this description.

        Unlike the value ledger these facts belong to one posting (the string —
        e.g. bare 'Remote' — says nothing); they survive rules resweeps and die
        when the posting's content changes (``purge_llm``). First writer wins.
        """
        from jobcrawler.runtime import geo

        del model  # recorded per-value in the ledger; posting rows carry source='llm'
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM position_locations "
                "WHERE site = ? AND url = ? AND source = 'llm_posting' LIMIT 1",
                (site, url),
            ).fetchone()
            if existing is not None:
                return False
            rows: list[dict[str, Any]] = []
            for p in places:
                place = {
                    "city": p.get("city"),
                    "subdivision": p.get("subdivision"),
                    "country": p.get("country"),
                    "macro": p.get("macro"),
                    "confidence": "medium",
                    "raw": "",
                }
                rows.append(
                    {
                        "kind": "place",
                        "city": place["city"],
                        "subdivision": place["subdivision"],
                        "country": place["country"],
                        "macro_region": place["macro"],
                        "display": _place_display_dict(place),
                        "confidence": "medium",
                    }
                )
            for f in fence:
                fd = _fence_dict(f)
                name = geo.country_name(fd.get("country")) or geo.macro_label(
                    fd.get("macro")
                )
                rows.append(
                    {
                        "kind": "remote_fence",
                        "country": fd.get("country"),
                        "macro_region": fd.get("macro"),
                        "display": f"Remote ({name})" if name else "Remote",
                        "confidence": "medium",
                    }
                )
            if remote == "worldwide":
                rows.append(
                    {
                        "kind": "remote_anywhere",
                        "macro_region": "worldwide",
                        "display": "Remote (Worldwide)",
                        "confidence": "medium",
                    }
                )
            if not rows:
                return False
            base = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS s FROM position_locations "
                "WHERE site = ? AND url = ?",
                (site, url),
            ).fetchone()["s"]
            self._conn.executemany(
                "INSERT INTO position_locations (site, url, seq, kind, city, "
                "subdivision, country, macro_region, display, source, confidence, "
                "raw_fragment) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'llm_posting', ?, NULL)",
                [
                    (site, url, base + 1 + i, r["kind"], r.get("city"),
                     r.get("subdivision"), r.get("country"), r.get("macro_region"),
                     r["display"], r["confidence"])
                    for i, r in enumerate(rows)
                ],
            )
            self._recompute_location_display_locked(site, url)
            self._conn.commit()
        return True

    def mark_location_extracted(self, site: str, url: str) -> None:
        """Stamp ``loc_llm_at``: the extract pass answered for this posting — even an
        empty answer — so the marker-arm gate never re-selects it for this content."""
        self._conn.execute(
            "UPDATE positions SET loc_llm_at = ? WHERE site = ? AND url = ?",
            (self._clock().isoformat(), site, url),
        )
        self._conn.commit()

    def location_llm_stamped(self, site: str, url: str) -> bool:
        """True when this posting's current content already has an extract answer —
        the §4.7 marker-arm gate reads it to keep re-runs free."""
        row = self._conn.execute(
            "SELECT loc_llm_at FROM positions WHERE site = ? AND url = ?",
            (site, url),
        ).fetchone()
        return bool(row is not None and row["loc_llm_at"])

    def position_locations(self, site: str, url: str) -> list[dict[str, Any]]:
        """The position's §4.12 projection rows in stated order (detail page + API)."""
        rows = self._conn.execute(
            "SELECT kind, city, subdivision, country, macro_region, display, source, "
            "confidence FROM position_locations WHERE site = ? AND url = ? ORDER BY seq",
            (site, url),
        ).fetchall()
        return [
            {
                "kind": r["kind"],
                "city": r["city"],
                "subdivision": r["subdivision"],
                "country": r["country"],
                "macro_region": r["macro_region"],
                "display": r["display"],
                "source": r["source"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    def _backfill_locations(self) -> None:
        """One sweep per GEO_PARSER_VERSION: parse every stored string, project every row.

        Covers ALL rows including closed ones (they resurface via search and role
        groups), recovers the 'Remote - US'-on-hybrid class as explicit fence rows at
        zero LLM cost, and re-evaluates LLM ledger rows against the upgraded parser
        (clean rules retire them; unresolved strings keep their paid answers). Runs
        inside the open-time migration — one commit, meta-guarded.
        """
        from jobcrawler.runtime import geo

        if self.get_meta("geo_parser_version") == str(geo.GEO_PARSER_VERSION):
            return
        rows = self._conn.execute(
            "SELECT site, url, location, locations_json, workplace FROM positions"
        ).fetchall()
        for row in rows:
            self._refresh_locations_locked(
                row["site"],
                row["url"],
                location=row["location"] or "",
                locations_json=row["locations_json"],
                workplace=row["workplace"],
                purge_llm=False,
            )
        self.set_meta("geo_parser_version", str(geo.GEO_PARSER_VERSION))

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

    def _effective_score_sql(self, *, column: str = "match_score", prefix: str = "") -> str:
        """SQL for the §4.7 freshness-decayed effective score: the base ``match_score`` fit
        scaled by a recency multiplier in ``[floor, 1]`` that decays with the posting's age
        (from ``posted_at``, else ``first_seen``). So a newer posting outranks an equally-good
        older one, while the floor keeps a great fit represented (never decays to nothing).
        NULL when the base is NULL (unscored). ``1.0`` floor disables decay (effective==base).

        Half-life/floor come from config; ``now`` is baked from the store's injected clock as
        a Julian-day literal so tests control it (``julianday()`` parses the ISO timestamps).
        Numeric literals only, never user input — matching the store's other f-string SQL.

        ``prefix`` qualifies the column references (``"p2."`` …) so the expression can
        score a specific table inside a correlated subquery, where the bare names would
        be ambiguous or bind to the wrong row.
        """
        from jobcrawler import config

        half_life = max(1e-6, config.score_half_life_days())
        floor = min(1.0, max(0.0, config.score_freshness_floor()))
        now_jd = 2440587.5 + self._clock().timestamp() / 86400.0
        age_days = (
            f"max(0.0, {now_jd!r} - julianday(COALESCE({prefix}posted_at, {prefix}first_seen)))"
        )
        freshness = (
            f"({floor!r} + {(1.0 - floor)!r} * pow(2.0, -({age_days}) / {half_life!r}))"
        )
        return f"{prefix}{column} * {freshness}"

    def matches(
        self,
        min_score: int,
        *,
        limit: int = 50,
        exclude_applied: bool = False,
        exclude_dismissed: bool = False,
        exclude_company_applied: bool = False,
        attainable_only: bool = False,
        collapse_duplicates: bool = False,
        exclude_cluster_handled: bool = False,
        collapse_role_groups: bool = False,
    ) -> list[PositionRecord]:
        """Open, scored positions at or above ``min_score``, best **effective** fit first (§4.7).

        Ranked and thresholded on the freshness-decayed *effective* score (base match fit ×
        a recency multiplier), not the raw fit — so "top matches" leads with fresh-and-strong
        while an old great fit slips only as far as the freshness floor. ``min_score`` is
        compared to the effective score too.

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

        ``attainable_only`` keeps only rows the operator could actually take from where
        they live: geographically unrestricted remote (``remote_scope = 'anywhere'``) or
        an explicit relocation/visa offer (``relocation = 'offered'``, both §4.6) — the
        dashboard shortlist's mobility gate. A row with neither signal drops, including
        the NULLs of an unclassified posting: absence of a fence is not evidence of reach.

        ``collapse_duplicates`` / ``exclude_cluster_handled`` / ``collapse_role_groups``
        are the §17 flags (see :meth:`search_positions`): one row per real-world job,
        no job whose cluster you've already dismissed or applied to, and one row per
        role group — here the score-first election is exactly the point, surfacing the
        location variant most worth pursuing.
        """
        clauses = ""
        if exclude_applied:
            clauses += "AND applied_at IS NULL "
        if collapse_duplicates:
            clauses += (
                "AND NOT EXISTS (SELECT 1 FROM position_clusters pc "
                "WHERE pc.site = positions.site AND pc.url = positions.url "
                "AND pc.is_canonical = 0) "
            )
        if collapse_role_groups:
            clauses += f"AND {self._role_group_primary_sql()} "
        if exclude_cluster_handled:
            clauses += (
                "AND NOT EXISTS (SELECT 1 FROM position_clusters me "
                "JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
                "JOIN positions p2 ON p2.site = sib.site AND p2.url = sib.url "
                "WHERE me.site = positions.site AND me.url = positions.url "
                "AND (p2.dismissed = 1 OR p2.applied_at IS NOT NULL)) "
            )
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
        if attainable_only:
            # NULLs fail both arms: an unclassified posting is not attainability evidence.
            clauses += "AND (remote_scope = 'anywhere' OR relocation = 'offered') "
        # Rank + threshold on the §4.7 freshness-decayed score, not the raw fit, so the
        # dashboard "top matches" leads with fresh-and-good and an old great fit slips only
        # as far as the floor allows. ``min_score`` is compared against the effective score.
        eff = self._effective_score_sql()
        rows = self._conn.execute(
            f"SELECT *, {eff} AS effective_score FROM positions "
            "WHERE status = 'open' AND match_score IS NOT NULL "
            # Threshold on the *rounded* effective score so it matches the integer shown in
            # the UI (a fresh 80 rounds up from 79.999… instead of just missing an >= 80 gate).
            f"AND round({eff}) >= ? "
            + clauses
            + "ORDER BY effective_score DESC, matched_at DESC, url LIMIT ?",
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
        # Bucket by the §4.7 freshness-decayed score so the histogram matches what the rest of
        # the dashboard ranks/shows. lo/hi are trusted ints from the caller's band table (not
        # user input), so the inline CASE is safe — mirrors the store's other f-string SQL.
        eff = self._effective_score_sql()
        # round() so a float effective score lands on the integer bands (a fresh 80 →
        # 79.999… would otherwise fall in the gap between the 60–79 and 80–100 bands).
        cases = " ".join(
            f"WHEN round({eff}) BETWEEN {int(lo)} AND {int(hi)} THEN {i}"
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
        origin: str | None = None,
    ) -> None:
        """Record a match verdict on a position and clear its pending flag (§6).

        ``matched_at`` is stamped from the clock. Applied both for a fresh LLM verdict and
        for a free cache hit — either way the row now carries the score and is no longer
        pending. ``reasons``/``concerns`` are opaque text to the store (the matcher
        serializes its rationale/concerns lists into them); persisting both keeps the full
        verdict on the durable row so a match trainset can be projected from it (§8).

        ``origin`` is §17 provenance: ``'llm'`` / ``'cache'`` for a normally scored row,
        ``'cluster:<site>\\n<url>'`` for a verdict inherited from a cluster sibling (the
        reversal handle for :meth:`requeue_inherited`). ``None`` leaves the column
        untouched for any legacy caller.
        """
        now = self._clock().isoformat()
        with self._lock:
            if origin is None:
                self._conn.execute(
                    "UPDATE positions SET match_score = ?, match_verdict = ?, "
                    "match_reasons = ?, match_concerns = ?, matched_at = ?, match_pending = 0 "
                    "WHERE site = ? AND url = ?",
                    (score, verdict, reasons, concerns, now, site, url),
                )
            else:
                self._conn.execute(
                    "UPDATE positions SET match_score = ?, match_verdict = ?, "
                    "match_reasons = ?, match_concerns = ?, matched_at = ?, match_pending = 0, "
                    "match_origin = ? WHERE site = ? AND url = ?",
                    (score, verdict, reasons, concerns, now, origin, site, url),
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
        never re-dedups a posting or invalidates its match verdict. The annualized-USD band
        is derived here too, so an LLM-backfilled salary is sortable/filterable like any
        other. Returns whether a row was actually filled (False = it already had a salary,
        or no such row).
        """
        from jobcrawler.runtime.normalize import usd_annual_band

        usd_min, usd_max = usd_annual_band(
            salary_min,
            salary_max,
            salary_currency,
            salary_period,
            fx_rates=self.fx_table(),
        )
        with self._lock:
            cur = self._conn.execute(
                "UPDATE positions SET salary_min = ?, salary_max = ?, "
                "salary_currency = ?, salary_period = ?, "
                "salary_usd_min = ?, salary_usd_max = ? "
                "WHERE site = ? AND url = ? "
                "AND salary_min IS NULL AND salary_max IS NULL",
                (
                    salary_min,
                    salary_max,
                    salary_currency,
                    salary_period,
                    usd_min,
                    usd_max,
                    site,
                    url,
                ),
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

        Cluster-canonical rows drain FIRST (§17): after a profile edit parks a whole
        cluster, the canonical copy rescores on budget and its siblings then inherit the
        fresh verdict for free instead of each paying. Standalone rows (no overlay row)
        rank with canonicals; within a rank the FIFO order is unchanged.
        """
        clause = (
            " AND description IS NOT NULL AND description != ''"
            if with_description_only
            else ""
        )
        rows = self._conn.execute(
            "SELECT positions.* FROM positions "
            "LEFT JOIN position_clusters pc ON pc.site = positions.site AND pc.url = positions.url "
            "WHERE status = 'open' AND match_pending = 1"
            f"{clause} ORDER BY COALESCE(pc.is_canonical, 1) DESC, "
            "first_seen ASC, positions.site, positions.url LIMIT ?",
            (limit,),
        ).fetchall()
        return [_to_position(r) for r in rows]

    # -- §17 cross-board duplicate clustering (read-derived overlay) ------------
    #
    # positions rows are observations; identity is an interpretation living in two
    # DISPOSABLE derived tables the reconciliation sweep rebuilds from rows alone
    # (jobcrawler.runtime.dedup). The store never derives keys itself — it persists
    # what the sweep/ingest hook computed and answers the sibling questions the
    # match/notify/UI gates ask. Nothing here writes positions lifecycle state.

    def replace_identity_keys(
        self, site: str, url: str, keys: Iterable[tuple[int, str]]
    ) -> None:
        """Replace one row's §17 identity keys (the failure-isolated ingest hook).

        An eager per-row write so a sibling upserted seconds earlier in the same crawl
        tick is already visible to the live match/notify gates — an optimization only;
        the sweep rebuilds every key regardless, so this is never a correctness
        dependency.
        """
        with self._lock:
            self._conn.execute(
                "DELETE FROM position_identity_keys WHERE site = ? AND url = ?",
                (site, url),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO position_identity_keys (site, url, tier, key) "
                "VALUES (?, ?, ?, ?)",
                [(site, url, tier, key) for tier, key in keys],
            )
            self._conn.commit()

    def dedup_sweep_rows(self) -> list[sqlite3.Row]:
        """Every position — open AND closed — with the fields the §17 sweep keys on.

        Closed rows are included on purpose: dismissed/applied suppression must survive
        board closure (a job you dismissed under a now-closed aggregator copy must not
        resurrect via a fresh board). ``desc_len`` substitutes for the multi-KB
        description (the election only compares lengths).
        """
        return self._conn.execute(
            "SELECT site, url, apply_url, external_id, company, company_canonical, "
            "title, location, location_normalized, content_hash, first_seen, status, "
            "applied_at, track_status, notes, dismissed, matched_at, match_verdict, "
            "LENGTH(COALESCE(description, '')) AS desc_len "
            "FROM positions ORDER BY site, url"
        ).fetchall()

    def rebuild_dedup_overlay(
        self,
        key_rows: Iterable[tuple[str, str, int, str]],
        cluster_rows: Iterable[tuple[str, str, str, int, int]],
        role_rows: Iterable[tuple[str, str, str]] = (),
    ) -> str:
        """Swap ALL overlay tables in one transaction; return the ``computed_at`` stamp.

        The single RLock'd connection means readers see the old overlay or the new one,
        never an empty in-between. DELETE + bulk INSERT (not incremental diffing) keeps
        the sweep idempotent and the overlay a pure projection. ``position_role_groups``
        is cleared unconditionally, so a sweep with the role-groups knob off leaves no
        stale groups behind.
        """
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute("DELETE FROM position_identity_keys")
            self._conn.execute("DELETE FROM position_clusters")
            self._conn.execute("DELETE FROM position_role_groups")
            self._conn.executemany(
                "INSERT OR IGNORE INTO position_identity_keys (site, url, tier, key) "
                "VALUES (?, ?, ?, ?)",
                list(key_rows),
            )
            self._conn.executemany(
                "INSERT OR REPLACE INTO position_clusters "
                "(site, url, cluster_id, is_canonical, tier, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(s, u, cid, canon, tier, now) for s, u, cid, canon, tier in cluster_rows],
            )
            self._conn.executemany(
                "INSERT OR REPLACE INTO position_role_groups "
                "(site, url, group_id, computed_at) VALUES (?, ?, ?, ?)",
                [(s, u, gid, now) for s, u, gid in role_rows],
            )
            self._conn.commit()
        return now

    def cluster_members(self, site: str, url: str) -> list[tuple[PositionRecord, bool]]:
        """All members of the row's materialized cluster (including itself) with their
        canonical flag, canonical first — or ``[]`` when the row is standalone."""
        rows = self._conn.execute(
            "SELECT p.*, sib.is_canonical AS _is_canonical FROM position_clusters me "
            "JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
            "JOIN positions p ON p.site = sib.site AND p.url = sib.url "
            "WHERE me.site = ? AND me.url = ? "
            "ORDER BY sib.is_canonical DESC, p.site, p.url",
            (site, url),
        ).fetchall()
        return [(_to_position(r), bool(r["_is_canonical"])) for r in rows]

    def cluster_sizes(
        self, pairs: Collection[tuple[str, str]]
    ) -> dict[tuple[str, str], int]:
        """Cluster size per (site, url), batched for a list page ("also on N boards").

        Rows not in any multi-row cluster are absent from the result (absence =
        standalone). One query per ≤400 pairs keeps parameter counts under SQLite's
        limit without a per-row lookup loop.
        """
        out: dict[tuple[str, str], int] = {}
        batch = [p for p in pairs]
        for start in range(0, len(batch), 400):
            chunk = batch[start : start + 400]
            values = ", ".join("(?, ?)" for _ in chunk)
            params: list[object] = []
            for s, u in chunk:
                params += [s, u]
            rows = self._conn.execute(
                f"WITH page(site, url) AS (VALUES {values}) "
                "SELECT page.site AS s, page.url AS u, COUNT(*) AS n "
                "FROM page "
                "JOIN position_clusters me ON me.site = page.site AND me.url = page.url "
                "JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
                "GROUP BY page.site, page.url",
                params,
            ).fetchall()
            for row in rows:
                out[(row["s"], row["u"])] = int(row["n"])
        return out

    def _role_group_primary_sql(self) -> str:
        """WHERE fragment keeping only each §17 role group's primary open member.

        An open row is hidden iff a strictly-better OPEN sibling shares its role group:
        better = higher rounded effective score (NULL ranks below any score), then
        earlier ``first_seen``, then ``(site, url)`` ascending — a total order over
        distinct rows, so exactly one member survives per group. Ungrouped and closed
        rows always pass. Query-time on purpose: verdicts land between sweeps, and the
        surfaced variant must follow them without waiting for the next rebuild.
        """
        eff_self = self._effective_score_sql(prefix="positions.")
        eff_sib = self._effective_score_sql(prefix="p2.")
        return (
            "(positions.status != 'open' OR NOT EXISTS ("
            "SELECT 1 FROM position_role_groups rg "
            "JOIN position_role_groups sib ON sib.group_id = rg.group_id "
            "AND (sib.site != rg.site OR sib.url != rg.url) "
            "JOIN positions p2 ON p2.site = sib.site AND p2.url = sib.url "
            "WHERE rg.site = positions.site AND rg.url = positions.url "
            "AND p2.status = 'open' AND ("
            f"COALESCE(round({eff_sib}), -1) > COALESCE(round({eff_self}), -1) "
            f"OR (COALESCE(round({eff_sib}), -1) = COALESCE(round({eff_self}), -1) "
            "AND (p2.first_seen < positions.first_seen "
            "OR (p2.first_seen = positions.first_seen "
            "AND (p2.site < positions.site "
            "OR (p2.site = positions.site AND p2.url < positions.url))))))))"
        )

    def role_group_variants(
        self, pairs: Collection[tuple[str, str]]
    ) -> dict[tuple[str, str], list[dict[str, str | None]]]:
        """Open location-variants per (site, url), batched for a list page's picker.

        For every listed row belonging to a §17 role group with 2+ open members, the
        full open membership (the row itself included) as light dicts — site, url,
        location fields, apply_url — ordered by location so the popup reads stably.
        Rows outside any group, or whose group has collapsed to one open member, are
        absent (absence = no picker). Same ≤400-pair batching as :meth:`cluster_sizes`.
        """
        out: dict[tuple[str, str], list[dict[str, str | None]]] = {}
        batch = list(pairs)
        for start in range(0, len(batch), 400):
            chunk = batch[start : start + 400]
            values = ", ".join("(?, ?)" for _ in chunk)
            params: list[object] = []
            for s, u in chunk:
                params += [s, u]
            rows = self._conn.execute(
                f"WITH page(site, url) AS (VALUES {values}) "
                "SELECT page.site AS s, page.url AS u, p.site, p.url, "
                "p.location, p.location_normalized, p.location_display, p.apply_url "
                "FROM page "
                "JOIN position_role_groups me ON me.site = page.site AND me.url = page.url "
                "JOIN position_role_groups sib ON sib.group_id = me.group_id "
                "JOIN positions p ON p.site = sib.site AND p.url = sib.url "
                "WHERE p.status = 'open' "
                # Ordering stays on the §17 bucket key (location_normalized-else-raw)
                # — location_display is a LABEL here, never the grouping/order source.
                "ORDER BY lower(COALESCE(NULLIF(p.location_normalized, ''), p.location, '')), "
                "p.site, p.url",
                params,
            ).fetchall()
            for row in rows:
                out.setdefault((row["s"], row["u"]), []).append(
                    {
                        "site": row["site"],
                        "url": row["url"],
                        "location": row["location"],
                        "location_normalized": row["location_normalized"],
                        "location_display": row["location_display"],
                        "apply_url": row["apply_url"],
                    }
                )
        return {k: v for k, v in out.items() if len(v) > 1}

    def role_group_members(self, site: str, url: str) -> list[PositionRecord]:
        """All OPEN members of the row's §17 role group (itself included), in location
        order — or ``[]`` when the row is ungrouped. Full records, unlike
        :meth:`role_group_variants`: the detail page's "other locations" panel shows
        score/lifecycle chips, which need the whole row."""
        rows = self._conn.execute(
            "SELECT p.* FROM position_role_groups me "
            "JOIN position_role_groups sib ON sib.group_id = me.group_id "
            "JOIN positions p ON p.site = sib.site AND p.url = sib.url "
            "WHERE me.site = ? AND me.url = ? AND p.status = 'open' "
            "ORDER BY lower(COALESCE(NULLIF(p.location_normalized, ''), p.location, '')), "
            "p.site, p.url",
            (site, url),
        ).fetchall()
        return [_to_position(r) for r in rows]

    def cluster_verdict_donor(
        self, site: str, url: str, *, min_desc_len: int, tiers: Collection[int]
    ) -> PositionRecord | None:
        """The best cluster sibling whose verdict a candidate may inherit, or None (§17).

        Sibling set = live identity-key join (edge-safe tiers only, blocklist excluded)
        ∪ the materialized cluster — live keys make a sibling scored seconds earlier in
        the same tick visible with no sweep latency. A donor must hold a real verdict
        (``matched_at`` set) and not be parked (``match_pending = 0`` — since a profile
        edit parks every open row, no stale-profile verdict can ever be inherited), and
        its description must be at least as informative as the candidate's up to the LLM
        digest cap (``min_desc_len``, computed by the caller) — a stub-derived verdict
        never overrides fuller text. Deterministic pick: longest description wins.
        """
        key_clause = _live_key_clause(tiers)
        row = self._conn.execute(
            "WITH sibs(site, url) AS ("
            "  SELECT k2.site, k2.url FROM position_identity_keys k1 "
            "  JOIN position_identity_keys k2 ON k2.key = k1.key "
            f"  WHERE k1.site = ? AND k1.url = ? AND {key_clause} "
            "    AND NOT (k2.site = k1.site AND k2.url = k1.url) "
            "    AND k1.key NOT IN (SELECT key FROM identity_key_blocklist) "
            "  UNION "
            "  SELECT sib.site, sib.url FROM position_clusters me "
            "  JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
            "  WHERE me.site = ? AND me.url = ? "
            "    AND NOT (sib.site = me.site AND sib.url = me.url)"
            ") "
            "SELECT p.* FROM sibs JOIN positions p ON p.site = sibs.site AND p.url = sibs.url "
            "WHERE p.matched_at IS NOT NULL AND p.match_pending = 0 "
            "  AND LENGTH(COALESCE(p.description, '')) >= ? "
            "ORDER BY LENGTH(COALESCE(p.description, '')) DESC, p.matched_at DESC, p.site, p.url "
            "LIMIT 1",
            (site, url, site, url, min_desc_len),
        ).fetchone()
        return _to_position(row) if row is not None else None

    def notify_gate(self, site: str, url: str, *, tiers: Collection[int]) -> bool:
        """True iff a ``match`` notification for this row should be sent (§17).

        Suppressed when the row itself is already handled (dismissed / applied) or has a
        ledger fact, or when ANY cluster sibling (live-key join ∪ materialized cluster)
        already notified or is dismissed / applied — one ping per real-world job, and a
        job you handled under one board never pings from another. Callers must
        :meth:`record_notified` after a successful emit so two siblings inside one drain
        batch dedup sequentially.
        """
        own = self._conn.execute(
            "SELECT 1 FROM notify_ledger WHERE event = 'match' AND site = ? AND url = ? "
            "UNION ALL "
            "SELECT 1 FROM positions WHERE site = ? AND url = ? "
            "AND (dismissed = 1 OR applied_at IS NOT NULL) LIMIT 1",
            (site, url, site, url),
        ).fetchone()
        if own is not None:
            return False
        key_clause = _live_key_clause(tiers)
        row = self._conn.execute(
            "WITH sibs(site, url) AS ("
            "  SELECT k2.site, k2.url FROM position_identity_keys k1 "
            "  JOIN position_identity_keys k2 ON k2.key = k1.key "
            f"  WHERE k1.site = ? AND k1.url = ? AND {key_clause} "
            "    AND NOT (k2.site = k1.site AND k2.url = k1.url) "
            "    AND k1.key NOT IN (SELECT key FROM identity_key_blocklist) "
            "  UNION "
            "  SELECT sib.site, sib.url FROM position_clusters me "
            "  JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
            "  WHERE me.site = ? AND me.url = ? "
            "    AND NOT (sib.site = me.site AND sib.url = me.url)"
            ") "
            "SELECT 1 FROM sibs WHERE "
            "EXISTS (SELECT 1 FROM notify_ledger nl WHERE nl.event = 'match' "
            "        AND nl.site = sibs.site AND nl.url = sibs.url) "
            "OR EXISTS (SELECT 1 FROM positions p WHERE p.site = sibs.site "
            "           AND p.url = sibs.url AND (p.dismissed = 1 OR p.applied_at IS NOT NULL)) "
            "LIMIT 1",
            (site, url, site, url),
        ).fetchone()
        return row is None

    def record_notified(self, event: str, site: str, url: str) -> None:
        """Append the per-row notification FACT (idempotent; facts are never derived)."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO notify_ledger (event, site, url, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (event, site, url, self._clock().isoformat()),
            )
            self._conn.commit()

    def requeue_inherited(self) -> int:
        """Reverse ALL §17 verdict inheritance: park every inherited row for rescoring.

        The full-reversal handle (``jobcrawl dedup --requeue-inherited``): rows whose
        verdict came from a cluster sibling re-enter the pending FIFO and re-score under
        the normal budget. Rows scored by LLM/cache are untouched.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE positions SET match_pending = 1, match_origin = NULL "
                "WHERE match_origin LIKE 'cluster:%'"
            )
            self._conn.commit()
            return cur.rowcount

    def block_identity_key(self, key: str, *, reason: str | None = None) -> None:
        """Blocklist an identity key: it forms no edges from the next sweep on, and the
        live sibling gates exclude it immediately. Splitting a group back to singletons
        is the safe failure direction. Re-blocking updates the reason, keeps the stamp."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO identity_key_blocklist (key, reason, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (key) DO UPDATE SET reason = excluded.reason",
                (key, reason, self._clock().isoformat()),
            )
            self._conn.commit()

    def unblock_identity_key(self, key: str) -> bool:
        """Remove a key from the blocklist; True if it was present."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM identity_key_blocklist WHERE key = ?", (key,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def blocked_keys(self) -> list[dict[str, str | None]]:
        """The full blocklist (key / reason / created_at), oldest first."""
        rows = self._conn.execute(
            "SELECT key, reason, created_at FROM identity_key_blocklist "
            "ORDER BY created_at, key"
        ).fetchall()
        return [
            {"key": r["key"], "reason": r["reason"], "created_at": r["created_at"]}
            for r in rows
        ]

    def blocked_key_set(self) -> set[str]:
        """Just the blocked key strings (the sweep's edge filter)."""
        rows = self._conn.execute("SELECT key FROM identity_key_blocklist").fetchall()
        return {r["key"] for r in rows}

    def dedup_overlay_stats(self) -> dict[str, int]:
        """Distinct-jobs arithmetic from the overlay (§17 dashboard KPI + report).

        ``distinct_open`` = open rows − open rows in clusters + clusters holding ≥1 open
        member: every standalone open row counts once, every cluster counts once.
        """

        def _one(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0] if row is not None and row[0] is not None else 0)

        open_rows = _one("SELECT COUNT(*) FROM positions WHERE status = 'open'")
        open_clustered = _one(
            "SELECT COUNT(*) FROM positions p JOIN position_clusters pc "
            "ON pc.site = p.site AND pc.url = p.url WHERE p.status = 'open'"
        )
        clusters_with_open = _one(
            "SELECT COUNT(DISTINCT pc.cluster_id) FROM positions p "
            "JOIN position_clusters pc ON pc.site = p.site AND pc.url = p.url "
            "WHERE p.status = 'open'"
        )
        return {
            "open_rows": open_rows,
            "open_clustered_rows": open_clustered,
            "clusters_with_open": clusters_with_open,
            "distinct_open": open_rows - open_clustered + clusters_with_open,
            "clusters": _one(
                "SELECT COUNT(DISTINCT cluster_id) FROM position_clusters"
            ),
            "clustered_rows": _one("SELECT COUNT(*) FROM position_clusters"),
        }

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
        eff = (
            self._effective_score_sql()
        )  # §4.7 freshness-decayed score for the detail view
        row = self._conn.execute(
            f"SELECT *, {eff} AS effective_score FROM positions WHERE site = ? AND url = ?",
            (site, url),
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
        relocation_offered: bool = False,
        country: str | None = None,
        remote_in: str | None = None,
        level: str | None = None,
        min_score: int | None = None,
        min_salary: float | None = None,
        dismissed: bool | None = None,
        applied: bool = False,
        exclude_company_applied: bool = False,
        collapse_duplicates: bool = False,
        exclude_cluster_handled: bool = False,
        collapse_role_groups: bool = False,
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
        - ``relocation_offered``  keep only rows whose posting states relocation/visa support
          (``relocation = 'offered'``, §4.6) — the "offers relocation" filter.
        - ``country``  ISO2 (case-folded): rows with ANY §4.12 resolved row in that country —
          an office there or remote fenced to it ("show me everything touching DE").
        - ``remote_in``  ISO2: rows REMOTELY WORKABLE from that country — kind-scoped to
          fence/worldwide rows (an office place never qualifies), with macro fences
          ('EMEA') expanded through ``geo_region_members``. Both are additive EXISTS
          filters: with no projection rows they simply match nothing.
        - ``min_score``  only rows whose **effective** (§4.7 freshness-decayed) score is at/above
          it (drops unscored); ``sort='score'`` likewise orders by the effective score, so an
          old great fit ranks below a fresh comparable one but stays above weaker leads.
        - ``min_salary``  ``COALESCE(salary_usd_max, salary_usd_min) >= min_salary``, i.e. an
          annualized-USD floor (§4.6) — drops rows with no resolvable salary/currency.
        - ``dismissed``  ``None`` = both, ``False`` = hide dismissed, ``True`` = only dismissed.
        - ``applied``  keep only rows you have actually applied to (``applied_at IS NOT NULL``,
          §16) — the "Applications" page. Survives a later ``rejected``/``withdrawn`` since the
          stamp is set on entering an applied-implying stage and never cleared (`tracking`).
        - ``exclude_company_applied``  drop rows whose hiring company already has an applied-to
          posting (grouped on ``company_canonical``, §16) — the browse twin of :meth:`matches`.

        ``sort`` ∈ {recent, score, updated, salary, title, applied}; NULL scores/salaries/dates
        sink last under a DESC sort. ``limit``/``offset`` paginate. Returns ``list[PositionRecord]``.

        §17 cluster flags (all default off ⇒ byte-identical queries):
        ``collapse_duplicates`` hides non-canonical cluster members (each real-world job
        shows once, via its elected representative; standalone rows are unaffected).
        ``exclude_cluster_handled`` hides every row whose cluster contains a dismissed or
        applied member — dismissing one copy retires the job everywhere at read time.
        ``collapse_role_groups`` shows one row per §17 role group (the same role opened
        in several locations): among a group's OPEN members, the one with the best
        rounded effective score wins — earliest ``first_seen`` then ``(site, url)``
        break ties, a total order, so exactly one survives. Election happens at query
        time, not sweep time, because verdicts land between sweeps and the surfaced
        variant should be the one most worth the operator's click. Closed rows never
        role-collapse (the group is a live-inventory affordance).
        **User-data auto-force**: a query filtered on the operator's own rows
        (``applied=True``, ``dismissed=True``, or an operator-set ``track_status``)
        bypasses all three — a row you applied to via an aggregator stays reachable in
        Applications forever. ``track_status='new'`` is exempt: 'new' is the schema
        default (untriaged inventory, the default browse view), not an operator action,
        and it is exactly the view collapsing exists for.
        """
        if applied or dismissed is True or (track_status and track_status != "new"):
            collapse_duplicates = False
            exclude_cluster_handled = False
            collapse_role_groups = False
        clauses: list[str] = []
        params: list[object] = []
        if collapse_duplicates:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM position_clusters pc "
                "WHERE pc.site = positions.site AND pc.url = positions.url "
                "AND pc.is_canonical = 0)"
            )
        if exclude_cluster_handled:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM position_clusters me "
                "JOIN position_clusters sib ON sib.cluster_id = me.cluster_id "
                "JOIN positions p2 ON p2.site = sib.site AND p2.url = sib.url "
                "WHERE me.site = positions.site AND me.url = positions.url "
                "AND (p2.dismissed = 1 OR p2.applied_at IS NOT NULL))"
            )
        if collapse_role_groups:
            clauses.append(self._role_group_primary_sql())
        q = query.strip()
        if q:
            like = f"%{q.lower()}%"
            clauses.append(
                "(lower(title) LIKE ? OR lower(company) LIKE ? "
                "OR lower(location) LIKE ? OR lower(COALESCE(location_normalized, '')) LIKE ? "
                # §4.12 synonym bridge: the display carries full English country names,
                # so q='brazil' hits a raw-'Brasil' row. Extends, never repoints.
                "OR lower(COALESCE(location_display, '')) LIKE ? "
                "OR lower(COALESCE(description, '')) LIKE ?)"
            )
            params += [like, like, like, like, like, like]
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
        if relocation_offered:
            clauses.append("lower(COALESCE(relocation, '')) = 'offered'")
        if country:
            clauses.append(
                "EXISTS (SELECT 1 FROM position_locations pl "
                "WHERE pl.site = positions.site AND pl.url = positions.url "
                "AND pl.country = ?)"
            )
            params.append(country.strip().upper())
        if remote_in:
            code = remote_in.strip().upper()
            clauses.append(
                "EXISTS (SELECT 1 FROM position_locations pl "
                "WHERE pl.site = positions.site AND pl.url = positions.url "
                "AND (pl.kind = 'remote_anywhere' "
                "OR (pl.kind = 'remote_fence' AND (pl.country = ? "
                "OR EXISTS (SELECT 1 FROM geo_region_members gm "
                "WHERE gm.region = pl.macro_region AND gm.country = ?)))))"
            )
            params += [code, code]
        if level:
            clauses.append("lower(COALESCE(level, '')) = ?")
            params.append(level.lower())
        eff = (
            self._effective_score_sql()
        )  # §4.7 freshness-decayed score (min_score + score sort)
        if min_score is not None:
            # Rounded to match the integer score the UI shows (see matches()).
            clauses.append(f"match_score IS NOT NULL AND round({eff}) >= ?")
            params.append(min_score)
        if min_salary is not None:
            clauses.append("COALESCE(salary_usd_max, salary_usd_min) >= ?")
            params.append(min_salary)
        if dismissed is not None:
            clauses.append("dismissed = ?")
            params.append(1 if dismissed else 0)
        if applied:
            clauses.append("applied_at IS NOT NULL")
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
            "score": "effective_score DESC, matched_at DESC, url",
            "updated": "track_updated_at DESC, first_seen DESC, url",
            "salary": "COALESCE(salary_usd_max, salary_usd_min) DESC, url",
            "title": "lower(title) ASC, url",
            "applied": "applied_at DESC, track_updated_at DESC, url",
        }.get(sort, "first_seen DESC, site, url")
        rows = self._conn.execute(
            f"SELECT *, {eff} AS effective_score FROM positions{where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
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

    def applied_counts(self) -> dict[str, int]:
        """Per-stage tally over positions you've actually applied to (``applied_at`` set, §16).

        The breakdown behind the "Applications" page: like :meth:`track_counts` but restricted
        to rows with an application on record, so a ``rejected``/``withdrawn`` count here means
        a role you applied to and lost — never one dismissed before you ever applied. Stages
        with no applied rows are absent (the web layer zero-fills from `tracking.ORDER`); the
        values sum to the page's total (``COUNT(*) WHERE applied_at IS NOT NULL``).
        """
        rows = self._conn.execute(
            "SELECT track_status, COUNT(*) AS n FROM positions "
            "WHERE applied_at IS NOT NULL GROUP BY track_status"
        ).fetchall()
        return {row["track_status"]: int(row["n"]) for row in rows}

    # -- crawler-generation log (§4.2) -----------------------------------------
    #
    # One row per generation *attempt* — its outcome plus the captured LLM interaction
    # log (generation is LLM-gated, so the model I/O is the record worth keeping). The
    # daemon's stdout dies with its terminal; this makes the "why did generation fail"
    # answer durable and queryable long after the run (the pain the 119-failure triage hit).

    def record_generation(
        self,
        site: str,
        *,
        outcome: str,
        reason: str | None = None,
        score: float | None = None,
        llm_log: list[dict[str, object]] | None = None,
        retain: int = 500,
    ) -> None:
        """Persist one §4.2 generation attempt: outcome + the captured LLM interaction log.

        ``outcome`` is ``promoted`` | ``staged`` | ``failed``; ``reason`` the top-level why
        (a gate reason, ``"no candidate produced"``, or an exception); ``score`` the winner's
        graded scalar (``None`` on failure); ``llm_log`` the list of ``{model, prompt,
        completion, error}`` interactions captured for the attempt, stored as JSON. ``retain``
        bounds table growth: rows older than the newest ``retain`` are pruned on insert (0 =
        keep everything). Recording is best-effort at the call site — it must never break a crawl.
        """
        calls = len(llm_log) if llm_log else 0
        payload = json.dumps(llm_log) if llm_log else None
        now = self._clock().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO generation_log "
                "(site, ts, outcome, reason, score, llm_calls, llm_log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (site, now, outcome, reason, score, calls, payload),
            )
            if retain:
                self._conn.execute(
                    "DELETE FROM generation_log WHERE id <= "
                    "(SELECT MAX(id) FROM generation_log) - ?",
                    (retain,),
                )
            self._conn.commit()

    def generation_log(
        self, site: str | None = None, *, limit: int = 50
    ) -> list[dict[str, object]]:
        """Recent §4.2 generation attempts, newest first; optionally filtered to one ``site``.

        Each row is a dict of the stored columns (``llm_log`` is the JSON string as persisted —
        the caller decodes it when it wants the interactions). Read-only; used by the
        ``jobcrawl generation-log`` reader.
        """
        if site is not None:
            rows = self._conn.execute(
                "SELECT * FROM generation_log WHERE site = ? ORDER BY id DESC LIMIT ?",
                (site, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM generation_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

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

    def request_crawl_all(self, slugs: Collection[str]) -> int:
        """Queue a manual crawl of every slug in one transaction (§16, "re-crawl all").

        The fleet-wide companion to :meth:`request_crawl`: forces every named site into the
        scheduler's next due set regardless of cadence, in a single write instead of N.
        Idempotent per slug (``ON CONFLICT`` refreshes ``requested_at``), so pressing the
        button twice before the daemon drains collapses to one request each. Does nothing
        itself; the daemon owns the crawl. Returns the number of slugs enqueued.
        """
        now = self._clock().isoformat()
        rows = [(slug, now) for slug in slugs if slug]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO crawl_requests (slug, requested_at) VALUES (?, ?) "
                "ON CONFLICT (slug) DO UPDATE SET requested_at = excluded.requested_at",
                rows,
            )
            self._conn.commit()
        return len(rows)

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

    # -- fx rates (currency → USD conversion factors, §4.6) --------------------

    def get_fx_rates(self) -> dict[str, float]:
        """Every stored currency → USD conversion factor; empty dict if none set.

        Persistence only — the factors are pulled out-of-band (a daily daemon job or
        ``jobcrawl fx-refresh``, see :mod:`jobcrawler.fx`) and read back here so the
        pure host-side salary conversion never touches the network (§4.6).
        """
        rows = self._conn.execute(
            "SELECT currency, rate_to_usd FROM fx_rates"
        ).fetchall()
        return {row["currency"]: row["rate_to_usd"] for row in rows}

    def set_fx_rates(self, rates: dict[str, float]) -> None:
        """Upsert the currency → USD conversion factors, stamping ``updated_at``.

        Idempotent per currency (``ON CONFLICT`` refreshes rate + timestamp); every row
        in one call shares the same ``updated_at`` so :meth:`fx_rates_age` reads a single
        freshness for the batch. A currency dropped from a later feed is *retained* (rows
        are only upserted, never deleted) — a superset never shrinks the majors floor.
        """
        now = self._clock().isoformat()
        rows = [(cur, rate, now) for cur, rate in rates.items()]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO fx_rates (currency, rate_to_usd, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT (currency) DO UPDATE SET "
                "rate_to_usd = excluded.rate_to_usd, updated_at = excluded.updated_at",
                rows,
            )
            self._conn.commit()

    def fx_rates_age(self) -> timedelta | None:
        """Time since the rates were last written, or None if never set.

        Reads the newest ``updated_at`` and subtracts it from the clock so a caller can
        judge staleness. ``updated_at`` is UTC ISO text (see :meth:`set_fx_rates`).
        """
        row = self._conn.execute(
            "SELECT MAX(updated_at) AS updated_at FROM fx_rates"
        ).fetchone()
        if row is None or row["updated_at"] is None:
            return None
        return self._clock() - datetime.fromisoformat(row["updated_at"])

    def fx_table(self) -> dict[str, float]:
        """Static conversion factors overlaid with any live rates (§4.6 salary→USD).

        The static :data:`FX_TO_USD` is the floor — it guarantees the handful of majors
        the free-text detector emits stay convertible even before the first live refresh —
        and persisted rates from :meth:`set_fx_rates` (a daily out-of-band pull, never the
        crawl path) override and extend it. Callers hand the result to
        :func:`usd_annual_band` so enrichment stays a pure function of its inputs.
        """
        from jobcrawler.runtime.normalize import FX_TO_USD

        live = self.get_fx_rates()
        return {**FX_TO_USD, **live} if live else dict(FX_TO_USD)

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

    def health_by_site(self) -> dict[str, str]:
        """Persisted §4.8 health state per site slug, in one query (§16 dashboard).

        Folds the per-site :meth:`get_health` loop the dashboard used to run (one query
        per registered board — hundreds on a full registry) into a single table scan.
        """
        rows = self._conn.execute("SELECT site, state FROM site_health").fetchall()
        return {row["site"]: row["state"] for row in rows}

    def new_positions_since(self, since_iso: str, *, collapse: bool = False) -> int:
        """Positions first seen at/after ``since_iso`` (§16 dashboard "new today" delta).

        With ``collapse`` (§17), counts distinct real-world jobs instead of rows: solo
        rows plus clusters, grouped by cluster id, where the group's EARLIEST first_seen
        is inside the window — so a late-arriving board copy of an old job no longer
        inflates "new since last visit".
        """
        if not collapse:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM positions WHERE first_seen >= ?",
                (since_iso,),
            ).fetchone()
            return int(row["n"])
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM ("
            "SELECT COALESCE(pc.cluster_id, positions.site || char(10) || positions.url) AS gid "
            "FROM positions LEFT JOIN position_clusters pc "
            "ON pc.site = positions.site AND pc.url = positions.url "
            "GROUP BY gid HAVING MIN(positions.first_seen) >= ?)",
            (since_iso,),
        ).fetchone()
        return int(row["n"])

    def health_updated_by_site(self) -> dict[str, str]:
        """Latest §4.8 health-state timestamp (ISO) per site slug, in one query.

        The fairness key for the §4.2 bootstrap and §4.8 regen sweeps: both order
        their backlog least-recently-attempted FIRST, and every assess outcome bumps
        this timestamp, so an attempted site structurally moves to the back of the
        queue. A slug absent here has never been attempted and sorts before
        everything (the sweeps treat a missing key as the empty string).
        """
        rows = self._conn.execute("SELECT site, updated_at FROM site_health").fetchall()
        return {row["site"]: row["updated_at"] for row in rows}

    def latest_run_by_site(self) -> dict[str, RunRecord]:
        """The most recent run per site slug, in one query (§16 dashboard).

        Replaces a per-site :meth:`last_run` loop; the ``runs_by_site`` index makes the
        per-site ``MAX(id)`` (each board's newest run) a cheap grouped lookup.
        """
        rows = self._conn.execute(
            "SELECT r.* FROM runs r JOIN ("
            "  SELECT site, MAX(id) AS mx FROM runs GROUP BY site"
            ") m ON r.site = m.site AND r.id = m.mx"
        ).fetchall()
        return {row["site"]: _to_record(row) for row in rows}

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


# -- §4.12 structured-location helpers (pure; shared by the store methods) -------


def _json_list(raw: str | None) -> list[str]:
    """Decode a locations_json array defensively — malformed input reads as empty."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [x for x in data if isinstance(x, str)] if isinstance(data, list) else []


def _parse_to_entry(parse: Any) -> dict[str, Any]:
    """Flatten a geo.LocationParse into the ledger's JSON-ready dict shape."""

    def _place(p: Any) -> dict[str, Any]:
        return {
            "city": p.city,
            "subdivision": p.subdivision,
            "country": p.country,
            "macro": p.macro,
            "confidence": p.confidence,
            "raw": p.raw,
        }

    return {
        "places": [_place(p) for p in parse.places],
        "fence": [_place(f) for f in parse.fence],
        "remote_marker": parse.remote_marker,
        "worldwide": parse.worldwide,
        "quality": parse.quality,
        "source": "rules",
    }


def _decode_ledger(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "places": json.loads(row["places_json"]),
        "fence": json.loads(row["fence_json"]),
        "remote_marker": bool(row["remote_marker"]),
        "worldwide": bool(row["worldwide"]),
        "quality": row["parse_quality"],
        "source": row["parse_source"],
    }


def _fence_dict(entry: str) -> dict[str, Any]:
    """An extract-pass fence token is an ISO2 code or a macro key; route by vocabulary."""
    from jobcrawler.runtime import geo

    token = (entry or "").strip()
    if token.upper() in geo.COUNTRY_CODES:
        return {"country": token.upper(), "macro": None, "confidence": "medium"}
    return {"country": None, "macro": token.lower(), "confidence": "medium"}


def _place_display_dict(place: dict[str, Any]) -> str:
    from jobcrawler.runtime import geo

    return geo.place_display(
        geo.GeoPlace(
            city=place.get("city"),
            subdivision=place.get("subdivision"),
            country=place.get("country"),
            macro=place.get("macro"),
            raw=place.get("raw") or "",
        )
    )


def _compose_projection(
    entries: list[dict[str, Any]], workplace: str | None
) -> list[dict[str, Any]]:
    """Merge the ledger entries of a position's strings into ordered projection rows.

    Places and fences dedup across strings. When the row is remote and the strings
    named only city-less places with no explicit fence, those places ARE the fence
    ('Spain' on a remote row means remote-fenced-to-Spain — the same reading
    ``classify_remote_scope`` gives the flat string), mirrored here so the
    structured rows never contradict the §4.6 enums.
    """
    from jobcrawler.runtime import geo

    places: list[dict[str, Any]] = []
    fences: list[dict[str, Any]] = []
    seen_places: set[tuple[Any, ...]] = set()
    seen_fences: set[tuple[Any, ...]] = set()
    worldwide = any(e["worldwide"] for e in entries)
    ww_source = "rules"
    if worldwide and all(
        e["source"] != "rules" for e in entries if e["worldwide"]
    ):
        ww_source = "llm_value"
    for entry in entries:
        # Projection provenance: 'rules' for parser-derived rows, 'llm_value' for
        # rows read out of a paid ledger fill. Both are disposable (re-derivable
        # from positions × location_values), unlike posting-local 'llm_posting'.
        row_source = "rules" if entry["source"] == "rules" else "llm_value"
        for place in entry["places"]:
            key = ((place.get("city") or "").lower(), place.get("subdivision"),
                   place.get("country"), place.get("macro"))
            if key not in seen_places:
                seen_places.add(key)
                places.append({**place, "source": row_source})
        for fence in entry["fence"]:
            key = (fence.get("country"), fence.get("macro"))
            if key not in seen_fences:
                seen_fences.add(key)
                fences.append({**fence, "source": row_source})

    if (
        workplace == "remote"
        and not fences
        and not worldwide
        and places
        and all(not p.get("city") and not p.get("subdivision") for p in places)
    ):
        fences = [
            {**p, "confidence": _worse_confidence(p.get("confidence"), "medium")}
            for p in places
        ]
        places = []

    rows: list[dict[str, Any]] = []
    for place in places:
        rows.append(
            {
                "kind": "place",
                "city": place.get("city"),
                "subdivision": place.get("subdivision"),
                "country": place.get("country"),
                "macro_region": place.get("macro"),
                "display": _place_display_dict(place),
                "source": place.get("source", "rules"),
                "confidence": place.get("confidence") or "high",
                "raw_fragment": place.get("raw") or None,
            }
        )
    for fence in fences:
        name = geo.country_name(fence.get("country")) or geo.macro_label(
            fence.get("macro")
        )
        rows.append(
            {
                "kind": "remote_fence",
                "country": fence.get("country"),
                "subdivision": None,
                "macro_region": fence.get("macro"),
                "display": f"Remote ({name})" if name else "Remote",
                "source": fence.get("source", "rules"),
                "confidence": fence.get("confidence") or "high",
                "raw_fragment": fence.get("raw") or None,
            }
        )
    if worldwide:
        rows.append(
            {
                "kind": "remote_anywhere",
                "macro_region": "worldwide",
                "display": "Remote (Worldwide)",
                "source": ww_source,
                "confidence": "high",
            }
        )
    return rows


def _worse_confidence(a: str | None, b: str) -> str:
    rank = {"high": 0, "medium": 1, "low": 2}
    left = a if a in rank else "high"
    return left if rank[left] >= rank[b] else b


def _compose_display(
    place_items: list[str], fence_names: list[str], anywhere: bool
) -> str | None:
    """One human line for the position row: places, then the fence, then worldwide.

    Long lists cap rather than scroll — 'Remote (A, B, C, D +18 more)' reads;
    twenty-two country names do not.
    """
    items: list[str] = []
    if len(place_items) > 5:
        items.extend(place_items[:5])
        items.append(f"+{len(place_items) - 5} more")
    else:
        items.extend(place_items)
    names = [n for n in fence_names if n]
    if names:
        if len(names) == 1:
            fenced = names[0]
        elif len(names) <= 4:
            fenced = ", ".join(names[:-1]) + " or " + names[-1]
        else:
            fenced = ", ".join(names[:4]) + f" +{len(names) - 4} more"
        items.append(f"Remote ({fenced})")
    if anywhere:
        items.append("Remote (Worldwide)")
    return " · ".join(items) if items else None


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
        relocation=row["relocation"],
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
        salary_usd_min=row["salary_usd_min"],
        salary_usd_max=row["salary_usd_max"],
        salary_raw=(row["salary_raw"] if "salary_raw" in row.keys() else None),
        salary_source=(
            row["salary_source"] if "salary_source" in row.keys() else None
        ),
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
        locations_json=(
            row["locations_json"] if "locations_json" in row.keys() else None
        ),
        location_display=(
            row["location_display"] if "location_display" in row.keys() else None
        ),
        loc_llm_at=(_dt("loc_llm_at") if "loc_llm_at" in row.keys() else None),
        match_origin=(row["match_origin"] if "match_origin" in row.keys() else None),
        # Present only when the query computed it (matches/search/get_position, §6).
        effective_score=(
            row["effective_score"] if "effective_score" in row.keys() else None
        ),
    )
