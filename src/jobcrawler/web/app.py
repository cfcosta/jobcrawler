"""FastAPI app for the position tracker (§16), over the shared §4.7 store.

Realizes the "optional thin HTTP surface (status, manual trigger)" that §4.9 reserved. A
separate ``jobcrawl serve`` process reads/writes the SAME WAL SQLite store the daemon owns
— safe because the store runs WAL + ``busy_timeout`` (one writer, many readers/occasional
writers don't corrupt each other). The UI exposes the operator-owned tracking axis
(`jobcrawler.tracking`): browse/search/filter, move a posting through your personal
pipeline, take notes, dismiss noise, plus a dashboard and manual crawl/rescore triggers.

Boundaries it must respect:

* It never runs a scheduler or calls an LLM. "Crawl now" flags a slug via
  :meth:`StateStore.request_crawl` for the daemon's ``due_sites`` to pick up (§4.9
  single-owner: the UI must not overlap a board's own run); "rescore" parks open rows via
  :meth:`StateStore.requeue_open_matches` for the daemon's match-drain to flush (§4.7 —
  no crawl needed, the description is persisted on the row).
* Per §8 the match verdict's ``reasons``/``concerns`` and the status/notes/dismissed axis
  are *operator-facing* in this UI — it never itself feeds them to a model. They are
  persisted on the durable row, and the offline, operator-invoked ``jobcrawl match-trainset``
  builder may project them (feedback overruling the stored verdict) into a matcher trainset
  for distilling a cheaper matcher. The crawler generator/optimizer still never reads them.
* Status *values* are validated host-side (`tracking.parse`) before the store persists the
  string; the store never adjudicates the value or the transition.

Handlers that touch the store are plain ``def`` (Starlette runs them in a threadpool), so
the blocking ``sqlite3`` calls — each already serialized by the store's own lock, with
``check_same_thread=False`` for the threadpool hop — never stall the event loop.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from jobcrawler import tracking
from jobcrawler.state import PositionRecord, StateStore

_log = logging.getLogger("jobcrawler.web")

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


def _asset_url(name: str) -> str:
    """Return ``/static/<name>`` stamped with the file's mtime (``?v=<mtime>``).

    Starlette's ``StaticFiles`` serves app.css/app.js with an etag + last-modified but no
    ``Cache-Control``, so browsers heuristically reuse a cached copy without revalidating.
    After an edit that silently kept running the *old* asset — e.g. a stale app.js in which
    the bulk-selection JS did not yet exist, so "select all" and the batch bar never worked.
    Stamping the mtime into the query string changes the URL whenever the file changes,
    forcing a fresh fetch while still allowing aggressive caching between edits. Read at
    render time so edits land without restarting the server.
    """
    try:
        version = int((_STATIC_DIR / name).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{name}?v={version}"


_PAGE_SIZE = 50
_BULK_ACTIONS = frozenset({"dismiss", "restore", "status", "note"})


# -- identity encoding ------------------------------------------------------
#
# A position's identity is ``(site, url)``; the url carries slashes and query strings, so
# it can't be a path segment. Encode the pair as urlsafe base64 of ``site\nurl`` for a
# clean, reversible ``/positions/{pid}`` path.


def encode_pid(site: str, url: str) -> str:
    """Reversible id for a position's ``(site, url)`` — urlsafe base64, padding stripped."""
    return base64.urlsafe_b64encode(f"{site}\n{url}".encode()).decode().rstrip("=")


def decode_pid(pid: str) -> tuple[str, str]:
    """Inverse of :func:`encode_pid`; raise 404 for anything that isn't a valid id."""
    pad = "=" * (-len(pid) % 4)
    try:
        raw = base64.urlsafe_b64decode(pid + pad).decode()
        site, url = raw.split("\n", 1)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=404, detail="unknown position id") from None
    return site, url


# -- small presentation helpers ---------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def reasons_list(raw: str | None) -> list[str]:
    """Decode the stored match reasons (a JSON list string, §4.7) to a list of strings."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return [raw]
    return [str(x) for x in data] if isinstance(data, list) else [str(data)]


def humanize_age(dt: datetime | None, *, now: datetime | None = None) -> str:
    """A compact age (``2h``/``3d``) since ``dt``; ``—`` when unknown, ``0s`` if future."""
    if dt is None:
        return "—"
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0.0, (now - dt).total_seconds())
    for unit, size in (("d", 86400.0), ("h", 3600.0), ("m", 60.0)):
        if secs >= size:
            return f"{int(secs // size)}{unit}"
    return f"{int(secs)}s"


def format_date(dt: datetime | None) -> str:
    """A calendar date (``2026-07-06``) for stamped events like ``applied_at``; ``—`` if unset."""
    return dt.date().isoformat() if dt is not None else "—"


def _compact_usd(value: float) -> str:
    """A short USD figure: ``$1.2M`` / ``$260k`` / ``$95`` (for hourly-derived lows)."""
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}k"
    return f"${value:.0f}"


def format_salary(p: PositionRecord) -> str:
    """A compact salary string from a record's ``salary_*`` fields; ``''`` when unstated.

    Shows the source range verbatim, and — when the source isn't USD — appends the
    annualized-USD equivalent (``~$…/yr``, §4.6) so a BRL/EUR/GBP figure is comparable
    at a glance with the dollar postings it sorts against.
    """
    lo, hi = p.salary_min, p.salary_max
    if lo is None and hi is None:
        return ""
    cur = (p.salary_currency or "").strip()
    body = (
        f"{lo:,.0f}–{hi:,.0f}"
        if lo is not None and hi is not None
        else f"{(hi if hi is not None else lo):,.0f}"
    )
    per = f"/{p.salary_period}" if p.salary_period else ""
    out = f"{cur} {body}{per}".strip()
    ulo, uhi = p.salary_usd_min, p.salary_usd_max
    if cur.upper() != "USD" and (ulo is not None or uhi is not None):
        if ulo is not None and uhi is not None and ulo != uhi:
            hint = f"{_compact_usd(ulo)}–{_compact_usd(uhi)}"
        else:
            single = uhi if uhi is not None else ulo
            assert single is not None  # the `or` guard above ensures one is set
            hint = _compact_usd(single)
        out += f" (~{hint}/yr)"
    return out


def _display_score(p: PositionRecord) -> int | None:
    """The score shown in the UI: the §4.7 freshness-decayed *effective* score (rounded), or
    the raw match fit when a query didn't compute the effective one, or None if unscored."""
    value = p.effective_score if p.effective_score is not None else p.match_score
    return round(value) if value is not None else None


def _position_dict(p: PositionRecord) -> dict[str, Any]:
    """A JSON-serialisable view of a position for the ``/api`` surface."""
    return {
        "pid": encode_pid(p.site, p.url),
        "site": p.site,
        "url": p.url,
        "apply_url": p.apply_url,
        "title": p.title,
        "company": p.company,
        "hiring_company": p.hiring_company,
        "company_canonical": p.company_canonical,
        "location": p.location,
        "location_normalized": p.location_normalized,
        "workplace": p.workplace,
        "remote_scope": p.remote_scope,
        "relocation": p.relocation,
        "level": p.level,
        "discipline": p.discipline,
        "employment_type": p.employment_type,
        "department": p.department,
        "salary": format_salary(p) or None,
        "salary_min": p.salary_min,
        "salary_max": p.salary_max,
        "salary_currency": p.salary_currency,
        "salary_period": p.salary_period,
        "salary_usd_min": p.salary_usd_min,
        "salary_usd_max": p.salary_usd_max,
        "description": p.description,
        "status": p.status,
        "closed_at": _iso(p.closed_at),
        "match_score": p.match_score,
        "effective_score": (
            round(p.effective_score, 1) if p.effective_score is not None else None
        ),
        "match_verdict": p.match_verdict,
        "match_reasons": reasons_list(p.match_reasons),
        "matched_at": _iso(p.matched_at),
        "track_status": p.track_status,
        "track_label": tracking.label(p.track_status),
        "applied_at": _iso(p.applied_at),
        "notes": p.notes,
        "dismissed": p.dismissed,
        "track_updated_at": _iso(p.track_updated_at),
        "posted_at": _iso(p.posted_at),
        "first_seen": _iso(p.first_seen),
        "last_seen": _iso(p.last_seen),
    }


def _int_param(params: Any, name: str) -> int | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float_param(params: Any, name: str) -> float | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_filters(params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse the browse query-string into (``search_positions`` kwargs, raw values).

    The raw dict re-populates the filter form so the UI reflects the active query. By
    default dismissed rows are hidden and both lifecycle states are shown (a tracker cares
    about a posting it applied to even after the board closes it).
    """

    def _s(name: str) -> str | None:
        v = params.get(name)
        v = v.strip() if isinstance(v, str) else v
        return v or None

    def _flag(name: str) -> bool:
        return (params.get(name) or "") in {"1", "true", "on", "yes"}

    show_dismissed = _flag("show_dismissed")
    hide_applied_companies = _flag("hide_applied_companies")
    applied = _flag("applied")
    remote_anywhere = _flag("remote_anywhere")
    relocation_offered = _flag("relocation_offered")
    status = _s("status")  # None/'all' => both open and closed
    track_status = _s("track_status")
    sort = _s("sort") or "score"
    kwargs: dict[str, Any] = {
        "query": (params.get("q") or "").strip(),
        "site": _s("site"),
        "company": _s("company"),
        "company_canonical": _s("company_canonical"),
        "status": status,
        "track_status": track_status,
        "workplace": _s("workplace"),
        # "Remote anywhere" is a geographic-scope filter over remote roles; scope
        # 'anywhere' only ever lands on a workplace='remote' row, so it stands alone.
        "remote_scope": "anywhere" if remote_anywhere else None,
        "relocation_offered": relocation_offered,
        "level": _s("level"),
        "min_score": _int_param(params, "min_score"),
        "min_salary": _float_param(params, "min_salary"),
        "dismissed": None if show_dismissed else False,
        "applied": applied,
        "exclude_company_applied": hide_applied_companies,
        "sort": sort,
    }
    raw = {
        "q": params.get("q") or "",
        "site": params.get("site") or "",
        "company": params.get("company") or "",
        "company_canonical": params.get("company_canonical") or "",
        "status": params.get("status") or "",
        "track_status": track_status or "",
        "workplace": params.get("workplace") or "",
        "level": params.get("level") or "",
        "min_score": params.get("min_score") or "",
        "min_salary": params.get("min_salary") or "",
        "sort": sort,
        "show_dismissed": show_dismissed,
        "hide_applied_companies": hide_applied_companies,
        "applied": applied,
        "remote_anywhere": remote_anywhere,
        "relocation_offered": relocation_offered,
    }
    return kwargs, raw


def _score_distribution(store: StateStore) -> list[dict[str, Any]]:
    """Bucketed histogram of open match scores, high band first (dashboard).

    The counts come from a single SQL aggregate (:meth:`StateStore.score_histogram`) —
    this used to load every open, scored row as a full record to bucket one integer,
    which dominated dashboard load time on a large corpus.
    """
    bands = [(80, 100), (60, 79), (40, 59), (20, 39), (0, 19)]
    counts = store.score_histogram(bands)
    peak = max(counts) if counts else 0
    return [
        {
            "label": f"{lo}–{hi}",
            "low": lo,
            "count": n,
            "pct": (100 * n // peak) if peak else 0,
            "strong": lo >= 60,
        }
        for (lo, hi), n in zip(bands, counts)
    ]


def _applied_funnel(store: StateStore) -> tuple[list[dict[str, Any]], int]:
    """The Applications page's per-stage breakdown + total (rows with ``applied_at`` set, §16).

    Always shows the applied-implying and terminal stages — a legible pipeline even when a
    stage is empty — plus any other stage a row was moved *back* to after applying, so the
    chip counts always sum to the total. Each chip links to the stage-filtered list.
    """
    counts = store.applied_counts()
    total = sum(counts.values())
    core = tracking.APPLIED_IMPLYING | tracking.TERMINAL
    funnel = [
        {
            "value": s.value,
            "label": tracking.LABELS[s],
            "token": tracking.CSS_TOKENS[s],
            "count": counts.get(s.value, 0),
            "terminal": s in tracking.TERMINAL,
        }
        for s in tracking.ORDER
        if s in core or counts.get(s.value, 0) > 0
    ]
    return funnel, total


# Coarse per-board coverage buckets for the dashboard, ordered attention-first (a failed
# board should sort above a healthy one). The raw §4.8 ``HealthState`` values collapse into
# these so the dashboard can show a legend + roll-up without importing the health enum.
_COV_RANK = {
    "failed": 0,
    "review": 1,
    "stale": 2,
    "live": 3,
    "disabled": 4,
    "pending": 5,
}


def _cov_state(enabled: bool, health: "str | None") -> str:
    """Fold a site's raw health string into a coarse coverage bucket (see :data:`_COV_RANK`).

    ``pending`` is the catch-all for a board with no recorded health yet (never crawled,
    or the healer hasn't classified it) — distinct from ``live`` so the dashboard can fold
    the not-yet-crawled long tail out of the main table.
    """
    if not enabled:
        return "disabled"
    if health in ("generation_failed", "blocked"):
        return "failed"
    if health == "degraded":
        return "review"
    if health == "stale":
        return "stale"
    if health == "healthy":
        return "live"
    return "pending"


def _bulk_msg(action: str, n: int, status_val: "tracking.TrackStatus | None") -> str:
    """A flash summary for a bulk apply (mirrors the single-row messages)."""
    noun = "position" if n == 1 else "positions"
    if action == "dismiss":
        return f"Dismissed {n} {noun}"
    if action == "restore":
        return f"Restored {n} {noun}"
    if action == "status" and status_val is not None:
        return f"{n} {noun} → {tracking.label(status_val.value)}"
    if action == "note":
        return f"Appended note to {n} {noun}"
    return f"Updated {n} {noun}"


def create_app(store: StateStore, *, sites: list[Any] | None = None) -> FastAPI:
    """Build the FastAPI app over an injected ``store`` (the DI seam the tests rely on).

    A ``":memory:"`` store is per-connection, so the app and its caller MUST share one
    instance — hence the factory rather than opening a store inside the handlers. ``sites``
    is the §4.1 registry (optional): it names the boards the crawl-now buttons can target
    and drives the dashboard's per-site panel; when absent those degrade gracefully.
    """
    app = FastAPI(title="jobcrawler — position tracker", docs_url="/api/docs")
    app.state.store = store
    site_list = list(sites or [])
    known_slugs = {getattr(s, "slug", None) for s in site_list}
    site_names = {
        getattr(s, "slug", ""): (
            getattr(s, "company", None)
            or getattr(s, "name", "")
            or getattr(s, "slug", "")
        )
        for s in site_list
    }

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    # Jinja's globals/filters are dynamically-typed namespaces; ty infers a narrow value
    # type from the built-in defaults, so cast to the real ``dict[str, Any]`` before adding
    # our template helpers (the tracking policy, the pid encoder, and presentation filters).
    jinja_globals = cast("dict[str, Any]", templates.env.globals)
    jinja_globals["tracking"] = tracking
    jinja_globals["encode_pid"] = encode_pid
    jinja_globals["site_names"] = site_names
    jinja_globals["asset"] = _asset_url
    jinja_globals["score_of"] = _display_score  # §4.7 freshness-decayed score for display
    jinja_filters = cast("dict[str, Any]", templates.env.filters)
    jinja_filters["reasons"] = reasons_list
    jinja_filters["age"] = humanize_age
    jinja_filters["salary"] = format_salary
    jinja_filters["date"] = format_date

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    def _require(site: str, url: str) -> PositionRecord:
        rec = store.get_position(site, url)
        if rec is None:
            raise HTTPException(status_code=404, detail="position not found")
        return rec

    def _back(
        request: Request, fallback: str, msg: str | None = None
    ) -> RedirectResponse:
        target = request.headers.get("referer") or fallback
        if msg:
            sep = "&" if "?" in target else "?"
            target = f"{target}{sep}msg={msg.replace(' ', '+')}"
        return RedirectResponse(target, status_code=303)

    def _apply_track(site: str, url: str, value: str) -> PositionRecord:
        status = tracking.parse(value)  # host-side validation (§8) — 400 on bad value
        store.set_track_status(
            site, url, status.value, stamp_applied=tracking.implies_applied(status)
        )
        return _require(site, url)

    def _apply_bulk(
        action: str, pids: list[str], *, track_status: str = "", note: str = ""
    ) -> tuple[int, "tracking.TrackStatus | None"]:
        """Apply one bulk ``action`` across ``pids`` via the per-row mutators; count hits.

        Reuses the exact single-row seams (``set_dismissed``/``set_track_status``/
        ``set_notes``) so the tracking axis stays host-adjudicated (§8). Unknown/stale ids
        (a row deleted or never present) are skipped, not fatal, so a partially-stale
        selection still applies to the rows that remain. The stage value is validated once,
        up front — a bad value raises ``ValueError`` before any write, so the batch is
        all-or-nothing on validation. ``note`` is *appended* (read-modify-write through
        ``set_notes``), newline-joined onto any existing notes.
        """
        status_val: tracking.TrackStatus | None = None
        if action == "status":
            status_val = tracking.parse(track_status)  # ValueError -> caller maps to 400
        body = note.strip()
        n = 0
        for pid in pids:
            site, url = decode_pid(pid)
            rec = store.get_position(site, url)
            if rec is None:
                continue
            if action == "dismiss":
                store.set_dismissed(site, url, True)
            elif action == "restore":
                store.set_dismissed(site, url, False)
            elif action == "status":
                assert status_val is not None
                store.set_track_status(
                    site,
                    url,
                    status_val.value,
                    stamp_applied=tracking.implies_applied(status_val),
                )
            elif action == "note":
                existing = (rec.notes or "").rstrip()
                store.set_notes(
                    site, url, f"{existing}\n{body}".strip() if existing else body
                )
            n += 1
        return n, status_val

    def _recrawl_all() -> tuple[int, int]:
        """Force a full refresh: queue every enabled site to crawl + park all open rows.

        Reuses the two existing single-owner primitives — :meth:`request_crawl` (idempotent
        per-slug enqueue the daemon drains via ``take_crawl_requests``) and
        :meth:`requeue_open_matches` (parks all open rows for the match-drain). It only
        *enqueues* work, so it's safe whether or not the daemon is up (requests persist)
        and never itself crawls or scores. Returns ``(sites_queued, rows_requeued)``.
        """
        slugs = [
            getattr(s, "slug", "")
            for s in site_list
            if getattr(s, "enabled", True) and getattr(s, "slug", "")
        ]
        return store.request_crawl_all(slugs), store.requeue_open_matches()

    # -- HTML: dashboard ----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Response:
        tcounts = store.track_counts()
        funnel = [
            {
                "value": s.value,
                "label": tracking.LABELS[s],
                "token": tracking.CSS_TOKENS[s],
                "count": tcounts.get(s.value, 0),
                "terminal": s in tracking.TERMINAL,
            }
            for s in tracking.ORDER
        ]
        lifecycle = store.position_counts()
        distribution = _score_distribution(store)
        strong = sum(b["count"] for b in distribution if b["strong"])

        # One query each for open counts / health / latest run, instead of a per-site
        # loop that fired hundreds of queries against a full registry.
        open_by_site = store.open_counts_by_site()
        health_by_site = store.health_by_site()
        runs_by_site = store.latest_run_by_site()
        per_site = []
        cov_counts = {k: 0 for k in _COV_RANK}
        for s in site_list:
            slug = getattr(s, "slug", "")
            enabled = getattr(s, "enabled", True)
            health = health_by_site.get(slug)
            state = _cov_state(enabled, health)
            cov_counts[state] += 1
            per_site.append(
                {
                    "slug": slug,
                    "name": site_names.get(slug, slug),
                    "enabled": enabled,
                    "open": open_by_site.get(slug, 0),
                    "health": health or "—",
                    "last_run": runs_by_site.get(slug),
                    "state": state,
                }
            )
        per_site.sort(
            key=lambda d: (_COV_RANK.get(str(d["state"]), 9), -int(d["open"]), str(d["name"]).lower())
        )
        # A board is "idle" (folded out of the table into a chip grid) only when it has
        # never surfaced anything: pending health AND zero open positions. Anything with
        # open roles stays in the table so its filtered-list link (#50) is reachable.
        visible = [d for d in per_site if d["state"] != "pending" or d["open"]]
        idle = [d for d in per_site if d["state"] == "pending" and not d["open"]]

        kpis = [
            {"label": "Open roles", "value": lifecycle["open"], "href": "/positions?status=open"},
            {"label": "Strong ≥60", "value": strong, "href": "/positions?min_score=60&sort=score", "accent": "success"},
            {"label": "Scored", "value": lifecycle["matched"], "href": "/positions?sort=score"},
            {"label": "Awaiting score", "value": lifecycle["pending"]},
            {"label": "Closed", "value": lifecycle["closed"], "href": "/positions?status=closed"},
            {"label": "Boards live", "value": cov_counts["live"], "sub": len(per_site), "href": "#sites"},
        ]

        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "nav": "dashboard",
                "msg": request.query_params.get("msg"),
                "kpis": kpis,
                "funnel": funnel,
                "dismissed": tcounts.get("dismissed", 0),
                "lifecycle": lifecycle,
                "distribution": distribution,
                "top_matches": store.matches(
                    0,
                    limit=8,
                    exclude_applied=True,
                    exclude_dismissed=True,
                    exclude_company_applied=True,
                ),
                "per_site": visible,
                "idle_sites": idle,
                "coverage": {"total": len(per_site), "counts": cov_counts, "idle": len(idle)},
                "has_registry": bool(site_list),
            },
        )

    # -- HTML: browse / search / filter -------------------------------------

    @app.get("/positions", response_class=HTMLResponse)
    def list_positions(request: Request) -> Response:
        kwargs, raw = _read_filters(request.query_params)
        page = max(1, _int_param(request.query_params, "page") or 1)
        rows = store.search_positions(
            **kwargs, limit=_PAGE_SIZE + 1, offset=(page - 1) * _PAGE_SIZE
        )
        has_next = len(rows) > _PAGE_SIZE
        rows = rows[:_PAGE_SIZE]
        url = request.url
        return templates.TemplateResponse(
            request=request,
            name="positions.html",
            context={
                "nav": "positions",
                "msg": request.query_params.get("msg"),
                "rows": rows,
                "filters": raw,
                "sites": site_list,
                "page": page,
                "has_next": has_next,
                "prev_url": str(url.include_query_params(page=page - 1))
                if page > 1
                else None,
                "next_url": str(url.include_query_params(page=page + 1))
                if has_next
                else None,
            },
        )

    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request) -> Response:
        """The personal application tracker (§16): only rows you've actually applied to.

        A focused view over ``search_positions(applied=True)`` — grouped-count funnel across
        the applied→outcome stages up top, the list below sorted newest-application-first, with
        the same inline stage dropdown as browse so a role advances through the pipeline right
        here. Dismissed rows are kept (an applied role you later hid is still one you applied to).
        """
        params = request.query_params
        stage = (params.get("stage") or "").strip() or None
        if stage is not None and not tracking.is_valid(stage):
            stage = None  # ignore a bogus stage rather than 400 a browse page
        sort = (params.get("sort") or "applied").strip() or "applied"
        page = max(1, _int_param(params, "page") or 1)
        rows = store.search_positions(
            applied=True,
            track_status=stage,
            dismissed=None,
            sort=sort,
            limit=_PAGE_SIZE + 1,
            offset=(page - 1) * _PAGE_SIZE,
        )
        has_next = len(rows) > _PAGE_SIZE
        rows = rows[:_PAGE_SIZE]
        funnel, total = _applied_funnel(store)
        url = request.url
        return templates.TemplateResponse(
            request=request,
            name="applications.html",
            context={
                "nav": "applications",
                "msg": params.get("msg"),
                "rows": rows,
                "funnel": funnel,
                "total": total,
                "stage": stage,
                "sort": sort,
                "page": page,
                "has_next": has_next,
                "prev_url": str(url.include_query_params(page=page - 1))
                if page > 1
                else None,
                "next_url": str(url.include_query_params(page=page + 1))
                if has_next
                else None,
            },
        )

    @app.get("/positions/{pid}", response_class=HTMLResponse)
    def position_detail(request: Request, pid: str) -> Response:
        site, url = decode_pid(pid)
        rec = _require(site, url)
        return templates.TemplateResponse(
            request=request,
            name="detail.html",
            context={
                "nav": "positions",
                "msg": request.query_params.get("msg"),
                "p": rec,
                "pid": pid,
                "reasons": reasons_list(rec.match_reasons),
                "concerns": reasons_list(rec.match_concerns),
                "crawlable": rec.site in known_slugs,
            },
        )

    # -- HTML: mutations (form POST -> redirect back) -----------------------

    @app.post("/positions/{pid}/status")
    def post_status(
        request: Request, pid: str, track_status: str = Form(...)
    ) -> Response:
        site, url = decode_pid(pid)
        _require(site, url)
        try:
            _apply_track(site, url, track_status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return _back(
            request, f"/positions/{pid}", f"Status → {tracking.label(track_status)}"
        )

    @app.post("/positions/{pid}/notes")
    def post_notes(request: Request, pid: str, notes: str = Form("")) -> Response:
        site, url = decode_pid(pid)
        _require(site, url)
        store.set_notes(site, url, notes)
        return _back(request, f"/positions/{pid}", "Notes saved")

    @app.post("/positions/{pid}/dismiss")
    def post_dismiss(
        request: Request, pid: str, dismissed: str = Form("1")
    ) -> Response:
        site, url = decode_pid(pid)
        _require(site, url)
        on = dismissed in {"1", "true", "on", "yes"}
        store.set_dismissed(site, url, on)
        return _back(request, f"/positions/{pid}", "Dismissed" if on else "Restored")

    @app.post("/positions/bulk")
    def post_bulk(
        request: Request,
        action: str = Form(...),
        pids: list[str] = Form(default=[]),
        track_status: str = Form(""),
        note: str = Form(""),
    ) -> Response:
        if action not in _BULK_ACTIONS:
            raise HTTPException(status_code=400, detail=f"unknown bulk action {action!r}")
        if not pids:
            return _back(request, "/positions", "No positions selected")
        if action == "note" and not note.strip():
            return _back(request, "/positions", "Nothing to append")
        try:
            n, status_val = _apply_bulk(
                action, pids, track_status=track_status, note=note
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return _back(request, "/positions", _bulk_msg(action, n, status_val))

    @app.post("/sites/{slug}/crawl")
    def post_crawl(request: Request, slug: str) -> Response:
        store.request_crawl(slug)
        known = slug in known_slugs or not site_list
        msg = (
            f"Crawl of {slug} queued for the daemon"
            if known
            else f"{slug} is not in the registry — request ignored by the daemon"
        )
        return _back(request, "/", msg)

    @app.post("/rescore")
    def post_rescore(request: Request) -> Response:
        n = store.requeue_open_matches()
        return _back(request, "/", f"Requeued {n} open positions for rescoring")

    @app.post("/recrawl-all")
    def post_recrawl_all(request: Request) -> Response:
        queued, requeued = _recrawl_all()
        if queued:
            msg = (
                f"Forced re-crawl of {queued} site(s) queued for the daemon; "
                f"requeued {requeued} open position(s) for rescoring"
            )
        else:
            msg = (
                f"No sites in the registry to re-crawl; "
                f"requeued {requeued} open position(s) for rescoring"
            )
        return _back(request, "/", msg)

    # -- JSON API (programmatic + tests) ------------------------------------

    @app.get("/api/positions")
    def api_positions(request: Request) -> JSONResponse:
        kwargs, _ = _read_filters(request.query_params)
        page = max(1, _int_param(request.query_params, "page") or 1)
        rows = store.search_positions(
            **kwargs, limit=_PAGE_SIZE, offset=(page - 1) * _PAGE_SIZE
        )
        return JSONResponse([_position_dict(p) for p in rows])

    @app.get("/api/positions/{pid}")
    def api_position(pid: str) -> JSONResponse:
        site, url = decode_pid(pid)
        return JSONResponse(_position_dict(_require(site, url)))

    @app.post("/api/positions/track")
    def api_track(body: "TrackBody") -> JSONResponse:
        _require(body.site, body.url)
        try:
            rec = _apply_track(body.site, body.url, body.track_status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(_position_dict(rec))

    @app.post("/api/positions/notes")
    def api_notes(body: "NotesBody") -> JSONResponse:
        _require(body.site, body.url)
        store.set_notes(body.site, body.url, body.notes)
        return JSONResponse(_position_dict(_require(body.site, body.url)))

    @app.post("/api/positions/dismiss")
    def api_dismiss(body: "DismissBody") -> JSONResponse:
        _require(body.site, body.url)
        store.set_dismissed(body.site, body.url, body.dismissed)
        return JSONResponse(_position_dict(_require(body.site, body.url)))

    @app.post("/api/positions/bulk")
    def api_bulk(body: "BulkBody") -> JSONResponse:
        if body.action not in _BULK_ACTIONS:
            raise HTTPException(
                status_code=400, detail=f"unknown bulk action {body.action!r}"
            )
        try:
            n, _ = _apply_bulk(
                body.action,
                body.pids,
                track_status=body.track_status or "",
                note=body.note or "",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse({"action": body.action, "updated": n})

    @app.post("/api/crawl", status_code=202)
    def api_crawl(body: "CrawlBody") -> JSONResponse:
        store.request_crawl(body.slug)
        known = body.slug in known_slugs or not site_list
        return JSONResponse({"queued": body.slug, "known": known}, status_code=202)

    @app.post("/api/rescore", status_code=202)
    def api_rescore() -> JSONResponse:
        return JSONResponse({"requeued": store.requeue_open_matches()}, status_code=202)

    @app.post("/api/recrawl-all", status_code=202)
    def api_recrawl_all() -> JSONResponse:
        queued, requeued = _recrawl_all()
        return JSONResponse({"queued": queued, "requeued": requeued}, status_code=202)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    return app


class TrackBody(BaseModel):
    site: str
    url: str
    track_status: str


class NotesBody(BaseModel):
    site: str
    url: str
    notes: str | None = None


class DismissBody(BaseModel):
    site: str
    url: str
    dismissed: bool = True


class BulkBody(BaseModel):
    pids: list[str]
    action: str
    track_status: str | None = None
    note: str | None = None


class CrawlBody(BaseModel):
    slug: str
