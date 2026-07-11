"""URL canonicalization — the one shared helper for dedup, gate-3, and SSRF.

A single canonical form keeps the §4.7 dedup key, the §6 gate-3 Jaccard, and
the §8 on-domain check consistent: two URLs that differ only by a tracking
param (``?utm_source=…``) must collapse to the same string, or a correct
crawler is wrongly rejected as unstable. ``normalize_host`` is the host-only
primitive the SSRF guard shares (it needs the host, not the query rules).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Exact param names dropped as tracking junk. ``utm_*`` is always dropped by
# prefix (below); this is the configurable remainder (JOBCRAWLER_TRACKING_PARAM_DENYLIST).
DEFAULT_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "_hsenc",
        "_hsmi",
    }
)

# Talent-marketplace / staffing-pool hosts where "apply" means joining a candidate
# pool, not applying to one job: every listing links to the same generic signup, so a
# posting whose effective apply target lands here carries no real per-employer
# application. Such a job is dropped at ingestion (JOBCRAWLER_MARKETPLACE_HOST_DENYLIST).
# NOT here: real ATS/board backends that host many companies but still mint a distinct
# per-job link (gupy.io, getro.com, greenhouse/lever/ashby) — those are legitimate.
# Suffix-matched by ``host_on_domain`` (subdomains included, attack-safe).
DEFAULT_MARKETPLACE_APPLY_HOSTS: frozenset[str] = frozenset(
    {
        # Confirmed pools — every observed listing's effective apply target collapses onto
        # ONE generic signup page (career.proxify.io/apply, lemon.io/for-developers/),
        # whatever the title. Verified against the tracked corpus, not eyeballed.
        "proxify.io",
        "lemon.io",
        # Well-known "join our talent network" marketplaces (preventive; apply == join the
        # pool, by business model — no per-job link exists to keep). No corpus rows yet, so
        # these are unverified insurance; drop any that later prove to mint per-job links.
        "toptal.com",
        "turing.com",
        "gun.io",
        "usebraintrust.com",
        "andela.com",
        "x-team.com",
        "crossover.com",
        "revelo.io",
        "strider.ai",
        # Aggregator the operator explicitly retired — re-lists others' roles behind its
        # own signup, no canonical per-employer link.
        "remoterocketship.com",
        # arc.dev — operator retired it: viewing a posting requires a signup, and its
        # "Arc Exclusive" listings carry no employer name or apply link at all. Its other
        # rows just bounce to LinkedIn reposts of roles that live elsewhere, so there's no
        # per-employer link worth keeping. (Reverses an earlier read of arc.dev as a real
        # per-job board — the signup wall makes it a pool.)
        "arc.dev",
        # a.team — fractional-talent marketplace the operator retired; its client
        # companies' boards are tracked directly instead, and aggregators (WWR,
        # remotive) re-list its pool postings, so any apply target landing here is a
        # signup, not a per-employer application.
        "a.team",
        # Deliberately NOT here, though it surfaced in the same sweep: innovecs.com mints a
        # distinct Greenhouse per-job link (jobs.innovecs.com/vacancies?gh_jid=…) — a real
        # per-job board, not a pool.
    }
)

# The same pools, keyed by HIRING COMPANY instead of apply host. Boards re-list
# marketplace roles with the marketplace as the company while the apply link points
# back at the BOARD itself, at nothing, or at a shortener — lemon.io reached the
# tracker all three ways (remotive/remoteok self-links, WWR with no apply link,
# nodesk behind cutt.ly), so the host test above never fired. The canonical company
# name is the shape-independent signal. Entries MUST be
# ``normalize.canonical_company`` outputs of the name boards print ("Lemon.io" →
# "lemonio", "X-Team" → "xteam"); anything else can never match. Same doctrine as
# the hosts: confirmed offenders plus the known pools as preventive insurance —
# drop any that later prove to be a distinct real employer. arc.dev is deliberately
# NOT here: canonical "arc" is too collision-prone a key, and its host entry
# already covers it (JOBCRAWLER_MARKETPLACE_COMPANY_DENYLIST overrides).
DEFAULT_MARKETPLACE_COMPANIES: frozenset[str] = frozenset(
    {
        # Confirmed in the corpus — 11 lemon.io rows arrived past the host filter.
        "lemonio",
        # Confirmed in the corpus — 4 A.Team rows arrived via WWR/remotive with the
        # marketplace as the printed company ("A.Team" → "ateam").
        "ateam",
        # Preventive: the canonical company forms of the pools host-listed above.
        "proxify",
        "toptal",
        "turing",
        "gunio",
        "braintrust",
        "andela",
        "xteam",
        "crossover",
        "revelo",
        "strider",
    }
)

_DEFAULT_PORTS = {"http": "80", "https": "443"}
_PCT = re.compile(r"%[0-9a-fA-F]{2}")


def _is_tracking(key: str, drop: Collection[str]) -> bool:
    k = key.lower()
    return k.startswith("utm_") or k in drop


def canonicalize_url(
    url: str, *, drop_params: Collection[str] = DEFAULT_TRACKING_PARAMS
) -> str:
    """Return the canonical form used as the dedup key (§4.6).

    Lowercases scheme + host, strips the default port, drops the fragment and
    tracking params, sorts the remaining query, normalizes the trailing slash,
    and uppercases percent-encodings. Path case is preserved (paths are
    case-sensitive). ``utm_*`` params are always dropped; ``drop_params``
    supplies the exact-name remainder.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()

    host = (parts.hostname or "").lower()
    # urlsplit().hostname strips the brackets around an IPv6 literal; re-add them
    # so the canonical form stays a valid, re-parseable URL. A ':' in hostname
    # only occurs for an IPv6 literal — the port is already separated out.
    host_lit = f"[{host}]" if ":" in host else host
    netloc = host_lit
    if parts.port is not None and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host_lit}:{parts.port}"

    path = _PCT.sub(lambda m: m.group().upper(), parts.path)
    path = path.rstrip("/") or "/"

    pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(k, drop_params)
    ]
    query = urlencode(sorted(pairs))

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_host(url: str) -> str:
    """Lowercased host without port — the SSRF on-domain primitive (§8)."""
    return (urlsplit(url.strip()).hostname or "").lower()


def host_on_domain(host: str, allowed: Collection[str]) -> bool:
    """True if ``host`` equals or is a subdomain of one of ``allowed`` (§8).

    The host-only on-domain primitive shared by the SSRF guard (§8) and the
    gate / scoring URL checks (§6). Suffix-attack safe: ``example.com.evil.com``
    is not on ``example.com``.
    """
    host = host.lower()
    return any(
        host == a or host.endswith("." + a) for a in (x.lower() for x in allowed)
    )


def valid_on_domain_url(url: str, allowed: Collection[str]) -> bool:
    """True if ``url`` is an http(s) URL whose host is on-domain (§6/§8)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    return host_on_domain(parts.hostname, allowed)


# A bare email address (``local@domain.tld``, no scheme/whitespace/path) — the shape an
# email-apply posting yields when the source prints an address rather than an <a href>.
_BARE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def sanitize_apply_url(apply_url: str | None) -> str | None:
    """Coerce a crawler's raw ``apply_url`` into a valid clickable link, or ``None``.

    The stored ``apply_url`` contract — assumed by the §16 web UI's ``<a href>`` and the
    apply-page resolver — is: an absolute ``http(s)`` or ``mailto:`` URL, else ``None``;
    never a bare email or a relative fragment. A crawler that reads an application address
    off a page can hand back a raw ``careers@acme.com`` (an email-apply posting) or a
    stray-whitespace URL; stored verbatim, the template renders it as a *relative* link
    that resolves under ``/positions/…`` and 404s.

    So: trim; keep an already-absolute ``http(s)``/``mailto:`` link; promote a bare email
    to ``mailto:``; drop anything else (a relative path, a Cloudflare email-protection
    stub, junk). A missing apply link is strictly better than a broken one — the
    posting's own ``url`` stays the fallback everywhere.
    """
    if not apply_url:
        return None
    raw = apply_url.strip()
    if not raw:
        return None
    if raw.lower().startswith(("http://", "https://", "mailto:")):
        return raw
    if _BARE_EMAIL.match(raw):
        return "mailto:" + raw
    return None
