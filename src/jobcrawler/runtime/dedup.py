"""Cross-board duplicate clustering (§17) — the read-derived identity overlay.

The same real-world opening is routinely stored several times: an ATS board row
plus aggregator copies. ``positions`` rows are *observations* ("board S said job
J at URL U"); real-world identity is an *interpretation* that lives entirely in
two disposable derived tables (``position_identity_keys``, ``position_clusters``)
rebuilt from the rows alone by :func:`resweep`. Dropping the overlay and
resweeping is lossless by construction.

Everything here is a **pure deterministic function of stored fields** — no LLM,
no network, no clock beyond the timestamp the caller stamps on the swap (§17
boundary). A false merge is worse than a missed merge, so every tier carries an
explicit precision contract and veto:

  T0  identical canonical ``url`` across sites          (~1.0 by construction)
  T1  ATS ``vendor:id`` from ``url``/``apply_url``      (org-conflict veto)
  T2  normalized ``apply_url`` equality                 (ATS-host or corroboration)
  T3  ``external_id`` within a declared slug family     (never global)
  T4  content_hash + company key, cross-site            (ambiguity veto; edges off)
  T5  (company key, GEO-gated title key), cross-site    (shadow only; edges off)
  T6  LinkedIn / Indeed posting ids                     (global)

Keys for ALL tiers are always computed and written (free shadow measurement);
which tiers may form *edges* is the caller's ``tiers`` set (host-side config,
never generator-visible). Two global constraints apply to every edge: the
higher-tier separator (rows carrying *different* T1 identities can never be
joined by a weaker tier) and the operator blocklist (a blocked key forms no
edges — splitting back to singletons is the safe failure direction).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import parse_qsl, urlsplit

from jobcrawler.runtime.normalize import normalize_title_key
from jobcrawler.urls import DEFAULT_TRACKING_PARAMS, canonicalize_url, host_on_domain

if TYPE_CHECKING:  # pragma: no cover - import cycle guard (state imports nothing from here)
    import sqlite3

    from jobcrawler.state import StateStore

log = logging.getLogger(__name__)

# Tier numbers — the wire values stored in position_identity_keys.tier /
# position_clusters.tier. NOT a strength order; see _TIER_STRENGTH.
TIER_URL = 0
TIER_ATS = 1
TIER_APPLY = 2
TIER_FAMILY = 3
TIER_CONTENT = 4
TIER_FUZZY = 5
TIER_LINKJOB = 6

# Evidence strength for "strongest tier evidencing membership" (position_clusters.tier)
# — exact per-posting ids first, then guarded equalities, then fuzzy.
_TIER_STRENGTH: dict[int, int] = {
    TIER_URL: 0,
    TIER_ATS: 1,
    TIER_LINKJOB: 2,
    TIER_APPLY: 3,
    TIER_FAMILY: 4,
    TIER_CONTENT: 5,
    TIER_FUZZY: 6,
}

# T2's OWN tracking-param denylist. A SEPARATE constant from
# ``urls.DEFAULT_TRACKING_PARAMS`` on purpose: that default canonicalizes the row
# PK ``url`` at ingest, so widening it would re-key existing rows. This one is only
# ever applied to ``apply_url`` inside key derivation. Entries are lowercase (the
# canonicalizer lowercases before matching). Identity-BEARING params are never
# listed here: gh_jid, ashby_jid, for, token, jk, pid, applyData.
IDENTITY_APPLY_TRACKING_PARAMS: frozenset[str] = frozenset(DEFAULT_TRACKING_PARAMS) | frozenset(
    {
        "ref",
        "source",
        "src",
        "gh_src",
        "trk",
        "t",
        "feedid",
        "jobboardsource",
        "refid",
        "trackingid",
        "position",
        "pagenum",
    }
)

# Hosts that mint a distinct per-job apply link — a normalized apply_url equality on
# one of these is identity without further corroboration (suffix-matched, so
# boards.greenhouse.io and job-boards.eu.greenhouse.io both land on greenhouse.io).
ATS_APPLY_HOSTS: frozenset[str] = frozenset(
    {
        "greenhouse.io",
        "lever.co",
        "ashbyhq.com",
        "smartrecruiters.com",
        "workable.com",
        "recruitee.com",
        "myworkdayjobs.com",
    }
)

# T3 scope: external_id is reliable only *within* a declared board family (one
# upstream feed crawled under several registry slugs). Never global — the measured
# '630' sourcegraph/temporal coincidence. Getro ids are global across its portfolio
# boards, so all Getro-backed slugs form one family. Registry-retired slugs (arc)
# stay listed: their rows persist in the DB and must keep clustering.
SLUG_FAMILIES: dict[str, frozenset[str]] = {
    "workingnomads": frozenset(
        {
            "working_nomads_development",
            "workingnomads_golang",
            "workingnomads_rust",
            "workingnomads_blockchain",
        }
    ),
    "jobicy": frozenset({"jobicy_developer", "jobicy_remote_dev"}),
    "arc": frozenset({"arc_rust", "arc_golang", "arc_web3"}),
    "getro": frozenset(
        {
            "sequoia_portfolio_jobs",
            "electric_capital",
            "framework_ventures",
            "framework_ventures_jobs",
            "btv_jobs",
            "polychain_capital",
            "coinbase_portfolio",
            "arbitrum",
            "index_ventures_jobs",
            "valor_capital",
            "paradigm_portfolio_jobs",
            "a16z_portfolio_jobs",
            "solana",
        }
    ),
}

# Sites whose ``company`` column does not hold an employer (HN threads carry
# locations/handles there) — excluded from every company-keyed tier (T4/T5).
COMPANY_UNRELIABLE_SITES: frozenset[str] = frozenset({"hn_whos_hiring", "hnhiring"})

# A key shared by more rows than this is degenerate (an extraction bug, not an
# identity) — it forms no edges and is surfaced in the report instead.
_DEGENERATE_KEY_ROWS = 100

# §17 role groups: a (company, title) key spanning more rows than this is a
# mass-poster's boilerplate title, not one role in many locations — grouping it
# would hide a page of inventory behind one list entry.
_ROLE_GROUP_CAP = 64

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_DIGITS_RE = re.compile(r"^\d+$")


class IdentityKey(NamedTuple):
    """One identity assertion about a row. ``org`` is T1 tenant evidence for the
    org-conflict veto — carried to the sweep, never part of the key string (the key
    must stay vendor+id so an org-less ``gh_jid=`` form joins the board form)."""

    tier: int
    key: str
    org: str | None = None


def _qs(url_parts: Any) -> list[tuple[str, str]]:
    return parse_qsl(url_parts.query, keep_blank_values=True)


def _greenhouse_keys(parts: Any, host: str, path_segs: list[str]) -> list[IdentityKey]:
    out: list[IdentityKey] = []

    def emit(job_id: str, org: str | None) -> None:
        job_id = job_id.strip()
        # Format guard: greenhouse job ids are all-digit and long; anything else is
        # a slug/junk capture and must not become a global key.
        if _DIGITS_RE.match(job_id) and len(job_id) >= 5:
            out.append(IdentityKey(TIER_ATS, f"ats:greenhouse:{job_id}", org))

    if host_on_domain(host, ("greenhouse.io",)):
        # boards.greenhouse.io/<org>/jobs/<id>, job-boards(.eu).greenhouse.io/…,
        # boards-api(.eu).greenhouse.io/v1/boards/<org>/jobs/<id>
        segs = path_segs
        if len(segs) >= 3 and segs[0] == "v1" and segs[1] == "boards":
            segs = segs[2:]
        if len(segs) >= 3 and segs[1] == "jobs":
            emit(segs[2], segs[0].lower())
        # embed form: /embed/job_app?for=<org>&token=<id>
        params = dict(_qs(parts))
        if "for" in params and "token" in params:
            emit(params["token"], params["for"].lower())
    # gh_jid=<digits> on ANY host (employer career pages embedding greenhouse).
    for k, v in _qs(parts):
        if k.lower() == "gh_jid":
            emit(v, None)
    return out


def _ashby_keys(parts: Any, host: str, path_segs: list[str]) -> list[IdentityKey]:
    out: list[IdentityKey] = []
    if host_on_domain(host, ("ashbyhq.com",)):
        # jobs.ashbyhq.com/<org>/<uuid>[/application]
        if len(path_segs) >= 2 and _UUID_RE.match(path_segs[1]):
            out.append(
                IdentityKey(
                    TIER_ATS,
                    f"ats:ashby:{path_segs[1].lower()}",
                    path_segs[0].lower(),
                )
            )
    for k, v in _qs(parts):
        if k.lower() == "ashby_jid" and _UUID_RE.match(v.strip()):
            out.append(IdentityKey(TIER_ATS, f"ats:ashby:{v.strip().lower()}", None))
    return out


def _lever_keys(host: str, path_segs: list[str]) -> list[IdentityKey]:
    # jobs(.eu).lever.co/<org>/<uuid>[/apply]
    if host_on_domain(host, ("lever.co",)) and len(path_segs) >= 2:
        if _UUID_RE.match(path_segs[1]):
            return [
                IdentityKey(
                    TIER_ATS,
                    f"ats:lever:{path_segs[1].lower()}",
                    path_segs[0].lower(),
                )
            ]
    return []


def _smartrecruiters_keys(host: str, path_segs: list[str]) -> list[IdentityKey]:
    out: list[IdentityKey] = []
    if host_on_domain(host, ("smartrecruiters.com",)):
        # jobs.smartrecruiters.com/<Org>/<postingId>[-slug];
        # api.smartrecruiters.com/v1/companies/<org>/postings/<id>
        segs = path_segs
        org: str | None = None
        posting: str | None = None
        if len(segs) >= 4 and segs[0] == "v1" and segs[1] == "companies" and segs[3] == "postings":
            org, posting = segs[2], segs[4] if len(segs) >= 5 else None
        elif len(segs) >= 2:
            org, posting = segs[0], segs[1]
        if posting:
            m = re.match(r"^(\d{9,})", posting)
            if m:
                out.append(
                    IdentityKey(
                        TIER_ATS,
                        f"ats:smartrecruiters:{m.group(1)}",
                        (org or "").lower() or None,
                    )
                )
    return out


def _workable_keys(host: str, path_segs: list[str]) -> list[IdentityKey]:
    # apply.workable.com/<org>/j/<SHORTCODE>[/...]
    if host_on_domain(host, ("workable.com",)) and len(path_segs) >= 3:
        if path_segs[1] == "j" and re.match(r"^[A-Za-z0-9]{6,}$", path_segs[2]):
            return [
                IdentityKey(
                    TIER_ATS,
                    f"ats:workable:{path_segs[2].lower()}",
                    path_segs[0].lower(),
                )
            ]
    return []


def _recruitee_keys(host: str, path_segs: list[str]) -> list[IdentityKey]:
    # <tenant>.recruitee.com/o/<slug> — slugs are only unique per tenant, so the
    # key is tenant-scoped (unlike the global-id vendors above).
    if host.endswith(".recruitee.com") and len(path_segs) >= 2 and path_segs[0] == "o":
        tenant = host[: -len(".recruitee.com")]
        if tenant and path_segs[1]:
            return [
                IdentityKey(
                    TIER_ATS, f"ats:recruitee:{tenant}:{path_segs[1].lower()}", tenant
                )
            ]
    return []


def _workday_keys(host: str, path_segs: list[str]) -> list[IdentityKey]:
    # <tenant>.wd<N>.myworkdayjobs.com/<site>/job/<location>/<Title>_<REQ-ID>
    m = re.match(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", host)
    if not m or not path_segs:
        return []
    tenant = m.group(1)
    last = path_segs[-1]
    req = last.rsplit("_", 1)[1] if "_" in last else last
    # Req ids look like JR-12345 / R-0012 / 12345 — require a digit so a bare
    # title segment (no req suffix) never becomes a key.
    if re.match(r"^[A-Za-z]{0,8}[-_]?\d[\w-]*$", req):
        return [IdentityKey(TIER_ATS, f"ats:workday:{tenant}:{req.lower()}", tenant)]
    return []


def _linkjob_keys(parts: Any, host: str, path_segs: list[str]) -> list[IdentityKey]:
    """T6: LinkedIn / Indeed posting ids. The regexes require a per-posting id, so
    search/listing URLs (no id) never key."""
    out: list[IdentityKey] = []
    if host_on_domain(host, ("linkedin.com",)):
        if len(path_segs) >= 3 and path_segs[0] == "jobs" and path_segs[1] == "view":
            m = re.search(r"(\d{8,})/?$", path_segs[2])
            if m:
                out.append(IdentityKey(TIER_LINKJOB, f"li:{m.group(1)}"))
        for k, v in _qs(parts):
            if k == "currentJobId" and _DIGITS_RE.match(v.strip()):
                out.append(IdentityKey(TIER_LINKJOB, f"li:{v.strip()}"))
    if host_on_domain(host, ("indeed.com",)):
        for k, v in _qs(parts):
            if k.lower() == "jk" and re.match(r"^[0-9a-f]{10,}$", v.strip(), re.IGNORECASE):
                out.append(IdentityKey(TIER_LINKJOB, f"indeed:{v.strip().lower()}"))
    return out


def ats_keys(url: str) -> list[IdentityKey]:
    """Every T1/T6 identity extractable from one URL (empty for junk/entity-escaped input)."""
    if not url:
        return []
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return []
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return []
    host = parts.hostname.lower()
    path_segs = [s for s in parts.path.split("/") if s]
    keys: list[IdentityKey] = []
    keys += _greenhouse_keys(parts, host, path_segs)
    keys += _ashby_keys(parts, host, path_segs)
    keys += _lever_keys(host, path_segs)
    keys += _smartrecruiters_keys(host, path_segs)
    keys += _workable_keys(host, path_segs)
    keys += _recruitee_keys(host, path_segs)
    keys += _workday_keys(host, path_segs)
    keys += _linkjob_keys(parts, host, path_segs)
    return keys


def normalize_apply_url(
    apply_url: str | None,
    *,
    own_url: str,
    marketplace_hosts: Collection[str] = (),
    drop_params: Collection[str] = IDENTITY_APPLY_TRACKING_PARAMS,
) -> str | None:
    """The T2 normalized apply target, or None when no sound key exists.

    Emission guards (all measured hazards): absolute http(s) with a host; not the
    row's own page (a same-page anchor is not cross-board evidence); not a talent-
    marketplace pool host (every listing there shares one generic signup). The
    normalization itself is :func:`jobcrawler.urls.canonicalize_url` under the
    SEPARATE §17 param denylist — identity-bearing params survive by construction.
    """
    if not apply_url or not apply_url.strip():
        return None
    raw = apply_url.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    try:
        own_host = (urlsplit(own_url.strip()).hostname or "").lower()
    except ValueError:
        own_host = ""
    if host == own_host:
        return None
    if marketplace_hosts and host_on_domain(host, marketplace_hosts):
        return None
    try:
        return canonicalize_url(raw, drop_params=drop_params)
    except ValueError:
        return None


def company_key(company: str | None, company_canonical: str | None) -> str:
    """The company grouping key — mirrors the SQL derivation
    ``COALESCE(NULLIF(company_canonical, ''), lower(company))`` used by the
    applied-company exclusion, so §17 groups exactly like §16 does."""
    ck = (company_canonical or "").strip()
    if ck:
        return ck
    return (company or "").strip().lower()


def identity_keys(
    site: str,
    url: str,
    *,
    apply_url: str | None,
    external_id: str | None,
    company: str | None,
    company_canonical: str | None,
    title: str | None,
    content_hash: str | None,
    marketplace_hosts: Collection[str] = (),
    apply_drop_params: Collection[str] = IDENTITY_APPLY_TRACKING_PARAMS,
    families: Mapping[str, Collection[str]] | None = None,
) -> list[IdentityKey]:
    """All identity keys for one row — every tier, deduplicated, deterministic.

    Pure function of stored fields (§17 boundary: no LLM, no network, no I/O).
    Emits keys for tiers whose *edges* may be disabled too — shadow measurement is
    free and the report reads it.
    """
    keys: list[IdentityKey] = []

    # T0: identical canonical URL. Rows are stored canonicalized at ingest, but
    # re-canonicalizing here is idempotent and covers rows that predate that.
    try:
        keys.append(IdentityKey(TIER_URL, f"url:{canonicalize_url(url)}"))
    except ValueError:
        keys.append(IdentityKey(TIER_URL, f"url:{url.strip()}"))

    # T1 + T6 from BOTH the listing URL and the apply target.
    keys += ats_keys(url)
    if apply_url:
        keys += ats_keys(apply_url)

    # T2: normalized apply target. Two key namespaces: 'applyats:' (per-job ATS
    # hosts — unconditionally edge-safe) vs 'apply:' (generic — needs sweep-time
    # company+title+single-candidate corroboration, and is never used by the live
    # sibling gates).
    norm = normalize_apply_url(
        apply_url,
        own_url=url,
        marketplace_hosts=marketplace_hosts,
        drop_params=apply_drop_params,
    )
    if norm:
        norm_host = (urlsplit(norm).hostname or "").lower()
        if host_on_domain(norm_host, ATS_APPLY_HOSTS):
            keys.append(IdentityKey(TIER_APPLY, f"applyats:{norm}"))
        else:
            keys.append(IdentityKey(TIER_APPLY, f"apply:{norm}"))

    # T3: external_id within a declared slug family only.
    ext = (external_id or "").strip()
    if ext:
        for family, slugs in (families or SLUG_FAMILIES).items():
            if site in slugs:
                keys.append(IdentityKey(TIER_FAMILY, f"fam:{family}:{ext}"))

    # T4/T5: company-keyed tiers — never for sites whose company column is
    # unreliable, never on a degenerate (<3 chars) company key.
    ck = company_key(company, company_canonical)
    if site not in COMPANY_UNRELIABLE_SITES and len(ck) >= 3:
        if content_hash:
            keys.append(IdentityKey(TIER_CONTENT, f"hash:{ck}:{content_hash}"))
        tk = normalize_title_key(title or "")
        if tk:
            keys.append(IdentityKey(TIER_FUZZY, f"fuzzy:{ck}:{tk}"))

    seen: set[tuple[int, str]] = set()
    out: list[IdentityKey] = []
    for k in keys:
        if (k.tier, k.key) not in seen:
            seen.add((k.tier, k.key))
            out.append(k)
    return out


# --- reconciliation sweep ----------------------------------------------------


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        p = self.parent
        while p[i] != i:
            p[i] = p[p[i]]
            i = p[i]
        return i

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        # Deterministic: lower index wins as root (input row order is sorted).
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        return True


def _cluster_id(root_site: str, root_url: str) -> str:
    digest = hashlib.sha256(f"{root_site}\n{root_url}".encode()).hexdigest()
    return "c" + digest[:16]


def _role_group_id(company_key_: str, title_key: str) -> str:
    digest = hashlib.sha256(f"{company_key_}\n{title_key}".encode()).hexdigest()
    return "rg" + digest[:16]


def _role_location(row: sqlite3.Row | Mapping[str, Any]) -> str:
    """Location bucket for §17 role grouping: normalized, else raw, else ``''``.

    Two empty-location rows share the ``''`` bucket — indistinguishable variants
    stay ungrouped (a picker of identical entries helps nobody), which also keeps
    same-city multiples (N identical openings at one office) as separate rows.
    """
    return (row["location_normalized"] or row["location"] or "").strip().lower()


def _election_key(row: Mapping[str, Any], direct_sites: Collection[str]) -> tuple:
    """Canonical (display representative) priority — min() wins.

    applied (earliest first) > operator-touched > open > the company's own board >
    longest description > (first_seen, site, url). Deterministic and recomputed
    every sweep, so a closed canonical is replaced as soon as a sibling is open.
    """
    applied_at = row["applied_at"]
    touched = (
        (row["track_status"] or "new") != "new"
        or bool(row["notes"])
        or bool(row["dismissed"])
    )
    return (
        0 if applied_at else 1,
        applied_at or "9999",
        0 if touched else 1,
        0 if row["status"] == "open" else 1,
        0 if row["site"] in direct_sites else 1,
        -(row["desc_len"] or 0),
        row["first_seen"],
        row["site"],
        row["url"],
    )


def resweep(
    store: StateStore,
    *,
    direct_sites: Collection[str],
    tiers: Collection[int],
    marketplace_hosts: Collection[str] = (),
    apply_drop_params: Collection[str] = IDENTITY_APPLY_TRACKING_PARAMS,
    families: Mapping[str, Collection[str]] | None = None,
    role_groups: bool = True,
) -> dict[str, Any]:
    """Rebuild the whole identity overlay from ``positions`` alone; return the report.

    Loads open AND closed rows (dismissed/applied suppression must survive board
    closure), derives every row's keys, forms guarded edges for the enabled
    ``tiers``, elects canonicals, and swaps the overlay tables in one transaction
    (readers see old-or-new, never empty). Idempotent; rerunnable forever.

    ``role_groups`` additionally bundles same-company/same-title OPEN postings that
    differ only by location into ``position_role_groups`` — a presentation-only
    projection (each member is a real, separately-applied-to posting; identity,
    verdicts, notify, and lifecycle never read it). ``False`` clears the table.
    """
    enabled = set(tiers)
    rows = store.dedup_sweep_rows()
    blocked = store.blocked_key_set()
    n = len(rows)

    row_keys: list[list[IdentityKey]] = []
    by_key: dict[str, list[int]] = {}
    key_tier: dict[str, int] = {}
    key_orgs: dict[str, set[str]] = {}
    keys_by_tier: Counter[int] = Counter()
    for i, row in enumerate(rows):
        keys = identity_keys(
            row["site"],
            row["url"],
            apply_url=row["apply_url"],
            external_id=row["external_id"],
            company=row["company"],
            company_canonical=row["company_canonical"],
            title=row["title"],
            content_hash=row["content_hash"],
            marketplace_hosts=marketplace_hosts,
            apply_drop_params=apply_drop_params,
            families=families,
        )
        row_keys.append(keys)
        for k in keys:
            by_key.setdefault(k.key, []).append(i)
            key_tier[k.key] = k.tier
            keys_by_tier[k.tier] += 1
            if k.tier == TIER_ATS and k.org:
                key_orgs.setdefault(k.key, set()).add(k.org)

    # T1 org-conflict veto: one (vendor, id) under two tenants voids the key —
    # and is auto-blocklisted so the live sibling gates can't join through it
    # between sweeps (blocking splits to singletons, the safe direction).
    org_conflicts = {k for k, orgs in key_orgs.items() if len(orgs) > 1}
    for k in sorted(org_conflicts - blocked):
        store.block_identity_key(
            k, reason=f"auto: t1 org conflict ({', '.join(sorted(key_orgs[k]))})"
        )
    blocked |= org_conflicts

    # Effective T1 identity per row (for the higher-tier separator) excludes
    # vetoed/blocked keys — a void identity neither joins nor separates.
    t1_of: list[frozenset[str]] = [
        frozenset(
            k.key for k in keys if k.tier == TIER_ATS and k.key not in blocked
        )
        for keys in row_keys
    ]
    ck_of = [company_key(r["company"], r["company_canonical"]) for r in rows]
    tk_of = [normalize_title_key(r["title"] or "") for r in rows]

    uf = _UnionFind(n)
    edge_tier_of: list[tuple[int, int] | None] = [None] * n  # (strength, tier)
    edges_by_tier: Counter[int] = Counter()
    guards = Counter(
        {
            "t1_org_conflict": len(org_conflicts),
            "t2_generic_rejects": 0,
            "t4_hash_ambiguous": 0,
            "t5_vetoes": 0,
            "separator_splits": 0,
            "blocklist_splits": 0,
            "degenerate_keys": 0,
            "role_group_cap_skips": 0,
        }
    )

    def mark(i: int, tier: int) -> None:
        rank = (_TIER_STRENGTH[tier], tier)
        prior = edge_tier_of[i]
        if prior is None or rank < prior:
            edge_tier_of[i] = rank

    def try_union(a: int, b: int, tier: int) -> None:
        # Higher-tier separator: differing T1 identities can never be fused by a
        # weaker tier — even a mistaken T2/T4/T5 edge cannot join two distinct reqs.
        if tier != TIER_ATS and t1_of[a] and t1_of[b] and t1_of[a].isdisjoint(t1_of[b]):
            guards["separator_splits"] += 1
            return
        uf.union(a, b)
        edges_by_tier[tier] += 1
        mark(a, tier)
        mark(b, tier)

    for key in sorted(by_key):
        idxs = by_key[key]
        if len(idxs) < 2:
            continue
        tier = key_tier[key]
        if key in blocked:
            guards["blocklist_splits"] += 1
            continue
        if tier not in enabled:
            continue
        if len(idxs) > _DEGENERATE_KEY_ROWS:
            guards["degenerate_keys"] += 1
            log.warning("dedup: degenerate key %r spans %d rows; skipped", key, len(idxs))
            continue
        idxs = sorted(idxs)
        generic_t2 = tier == TIER_APPLY and key.startswith("apply:")
        if tier == TIER_CONTENT:
            # Ambiguity veto: one site holding the hash twice = same text, two live
            # reqs (the anthropic/ramp shape) — the hash is not identity here.
            per_site = Counter(rows[i]["site"] for i in idxs)
            if any(c > 1 for c in per_site.values()):
                guards["t4_hash_ambiguous"] += 1
                continue
        t2_site_counts = Counter(rows[i]["site"] for i in idxs) if generic_t2 else None
        t5_open_counts = (
            Counter(rows[i]["site"] for i in idxs if rows[i]["status"] == "open")
            if tier == TIER_FUZZY
            else None
        )
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                a, b = idxs[x], idxs[y]
                if tier in (TIER_CONTENT, TIER_FUZZY) and rows[a]["site"] == rows[b]["site"]:
                    continue  # cross-site only, never within one board
                if generic_t2:
                    # Generic apply link: corroboration required — same company key,
                    # same normalized title, and each side the single candidate on
                    # its site (kills the shared-signup-form groups).
                    assert t2_site_counts is not None
                    if (
                        t2_site_counts[rows[a]["site"]] > 1
                        or t2_site_counts[rows[b]["site"]] > 1
                        or not ck_of[a]
                        or ck_of[a] != ck_of[b]
                        or not tk_of[a]
                        or tk_of[a] != tk_of[b]
                    ):
                        guards["t2_generic_rejects"] += 1
                        continue
                if tier == TIER_FUZZY:
                    assert t5_open_counts is not None
                    if (
                        t5_open_counts[rows[a]["site"]] > 1
                        or t5_open_counts[rows[b]["site"]] > 1
                        or not _locations_compatible(rows[a], rows[b])
                    ):
                        guards["t5_vetoes"] += 1
                        continue
                try_union(a, b, tier)

    # Components → materialized clusters (size ≥ 2; absence = standalone row).
    comps: dict[int, list[int]] = {}
    for i in range(n):
        comps.setdefault(uf.find(i), []).append(i)
    clusters = [sorted(members) for members in comps.values() if len(members) >= 2]

    key_rows: list[tuple[str, str, int, str]] = []
    for i, keys in enumerate(row_keys):
        for k in keys:
            key_rows.append((rows[i]["site"], rows[i]["url"], k.tier, k.key))

    cluster_rows: list[tuple[str, str, str, int, int]] = []
    multi_matched = 0
    conflicting = 0
    leak_rows = 0
    open_in_clusters = 0
    clusters_with_open = 0
    shadowed: set[int] = set()  # non-canonical members: hidden in the collapsed list
    top: list[dict[str, Any]] = []
    for members in clusters:
        root = min(members, key=lambda i: (rows[i]["first_seen"], rows[i]["site"], rows[i]["url"]))
        cid = _cluster_id(rows[root]["site"], rows[root]["url"])
        canonical = min(members, key=lambda i: _election_key(rows[i], direct_sites))
        shadowed.update(i for i in members if i != canonical)
        for i in members:
            rank = edge_tier_of[i]
            cluster_rows.append(
                (
                    rows[i]["site"],
                    rows[i]["url"],
                    cid,
                    1 if i == canonical else 0,
                    rank[1] if rank else TIER_FUZZY,
                )
            )
        verdicts = {
            rows[i]["match_verdict"] for i in members if rows[i]["matched_at"]
        }
        matched = sum(1 for i in members if rows[i]["matched_at"])
        if matched > 1:
            multi_matched += 1
        if len(verdicts) > 1:
            conflicting += 1
        handled = any(rows[i]["dismissed"] or rows[i]["applied_at"] for i in members)
        opens = [i for i in members if rows[i]["status"] == "open"]
        open_in_clusters += len(opens)
        if opens:
            clusters_with_open += 1
        if handled:
            leak_rows += sum(
                1
                for i in opens
                if not rows[i]["dismissed"] and not rows[i]["applied_at"]
            )
        top.append(
            {
                "cluster_id": cid,
                "size": len(members),
                "company": rows[canonical]["company"],
                "title": rows[canonical]["title"],
                "sites": sorted({rows[i]["site"] for i in members}),
            }
        )
    top.sort(key=lambda c: (-c["size"], c["cluster_id"]))

    # §17 role groups: bundle the same role opened in several locations. Groups are
    # keyed on (company key, title key) over what the collapsed list actually shows —
    # open cluster-canonicals and open standalone rows (a shadowed duplicate is
    # already hidden; grouping it too would double-count the role). Members are real
    # distinct postings, so this projection is presentation-only by contract.
    role_rows: list[tuple[str, str, str]] = []
    role_group_count = 0
    role_grouped_rows = 0
    if role_groups:
        by_role: dict[tuple[str, str], list[int]] = {}
        for i in range(n):
            if i in shadowed or rows[i]["status"] != "open":
                continue
            ck, tk = ck_of[i], tk_of[i]
            if ck and tk:
                by_role.setdefault((ck, tk), []).append(i)
        for (ck, tk), members in sorted(by_role.items()):
            if len(members) < 2:
                continue
            if len(members) > _ROLE_GROUP_CAP:
                guards["role_group_cap_skips"] += 1
                log.warning(
                    "dedup: role key (%r, %r) spans %d rows; skipped", ck, tk, len(members)
                )
                continue
            if len({_role_location(rows[i]) for i in members}) < 2:
                continue  # same-location multiples are separate openings, not variants
            gid = _role_group_id(ck, tk)
            role_group_count += 1
            role_grouped_rows += len(members)
            role_rows.extend((rows[i]["site"], rows[i]["url"], gid) for i in members)

    computed_at = store.rebuild_dedup_overlay(key_rows, cluster_rows, role_rows)

    report: dict[str, Any] = {
        "swept_at": computed_at,
        "rows": n,
        "open_rows": sum(1 for r in rows if r["status"] == "open"),
        "tiers_enabled": sorted(enabled),
        "clusters": len(clusters),
        "clustered_rows": sum(len(m) for m in clusters),
        "collapse_saving_open": open_in_clusters - clusters_with_open,
        "role_groups": role_group_count,
        "role_grouped_rows": role_grouped_rows,
        "role_collapse_saving": role_grouped_rows - role_group_count,
        "keys_by_tier": {str(t): keys_by_tier[t] for t in sorted(keys_by_tier)},
        "edges_by_tier": {str(t): edges_by_tier[t] for t in sorted(edges_by_tier)},
        "guards": dict(guards),
        "multi_matched_clusters": multi_matched,
        "conflicting_verdict_clusters": conflicting,
        "dismissal_leak_rows": leak_rows,
        "top_clusters": top[:20],
    }
    store.set_meta("dedup_last_report", json.dumps(report))
    return report


_REMOTEISH = re.compile(r"remote|anywhere|worldwide|global|distributed", re.IGNORECASE)


def _locations_compatible(
    a: sqlite3.Row | Mapping[str, Any], b: sqlite3.Row | Mapping[str, Any]
) -> bool:
    """T5 location gate: equal normalized locations, or either side remote/missing."""
    la = (a["location_normalized"] or a["location"] or "").strip().lower()
    lb = (b["location_normalized"] or b["location"] or "").strip().lower()
    if not la or not lb:
        return True
    if la == lb:
        return True
    return bool(_REMOTEISH.search(la) or _REMOTEISH.search(lb))
