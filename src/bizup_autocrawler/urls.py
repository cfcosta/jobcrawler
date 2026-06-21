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
# prefix (below); this is the configurable remainder (AUTOCRAWLER_TRACKING_PARAM_DENYLIST).
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
