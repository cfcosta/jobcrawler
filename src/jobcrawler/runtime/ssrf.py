"""The SSRF guard (§8): keep crawler fetches on-domain and off internal targets.

``ctx`` calls ``check_url`` before every request (and on every redirect hop). Two
independent rules: the target host must be one of the site's registered hosts (or
a subdomain), and it must not be an internal address — private/loopback/
link-local/reserved IPs or a cloud-metadata endpoint — even if the operator
listed it. This blocks **literal**-IP and known-metadata targets per hop. Re-checking the
*resolved* IP (the DNS-rebinding defence — a registered name that resolves to an
internal address) is not done here in ``check_url``, which validates the name only;
that resolve-and-pin step lives in the #19b egress proxy, which resolves in the
trusted parent and screens every candidate through :func:`screen_resolved_ip`
before the child ever connects. ``check_url`` stays the per-hop name/literal belt.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection

from jobcrawler.urls import host_on_domain as is_on_domain
from jobcrawler.urls import normalize_host

__all__ = ["SsrfError", "check_url", "is_on_domain", "screen_resolved_ip"]

# Hostnames that resolve to cloud-metadata services (the IP forms are caught by
# the link-local check below).
_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata"})


class SsrfError(ValueError):
    """A fetch target failed the on-domain / no-internal-target guard."""


def _is_blocked_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # An IPv4-mapped IPv6 literal (``::ffff:169.254.169.254``) reports its class flags
    # against the v6 wrapper, not the embedded v4 — so metadata/RFC1918 mapped into v6
    # would slip every predicate below. Unwrap to the embedded v4 first so the mapped
    # form is screened identically to the bare v4 (belt for a resolver returning it).
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def screen_resolved_ip(ip_str: str) -> bool:
    """``True`` if a *resolved* IP literal is a safe public target (#19b resolve-and-pin).

    The egress proxy resolves an allowlisted hostname in the trusted parent and calls
    this on every candidate address before dialing — so a name that (re)resolves to a
    private / loopback / link-local / reserved / multicast / cloud-metadata IP is dropped
    at connect time, closing the DNS-rebinding window ``check_url`` (name-only) leaves
    open. Returns ``False`` for an unparseable string (fail closed)."""
    try:
        ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not _is_blocked_ip(ip_str)


def check_url(url: str, allowed_hosts: Collection[str]) -> None:
    """Raise :class:`SsrfError` unless ``url``'s host is on-domain and external."""
    host = normalize_host(url)
    if not host:
        raise SsrfError(f"no host in url: {url!r}")
    if host in _BLOCKED_HOSTNAMES or _is_blocked_ip(host):
        raise SsrfError(f"blocked internal target: {host}")
    if not is_on_domain(host, allowed_hosts):
        raise SsrfError(f"off-domain host {host!r} not in {sorted(allowed_hosts)}")
