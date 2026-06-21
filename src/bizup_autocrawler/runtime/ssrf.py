"""The SSRF guard (§8): keep crawler fetches on-domain and off internal targets.

``ctx`` calls ``check_url`` before every request. Two independent rules: the
target host must be one of the site's registered hosts (or a subdomain), and it
must not be an internal address — private/loopback/link-local/reserved IPs or a
cloud-metadata endpoint — even if the operator listed it. (Full DNS-rebinding
defence resolves the host and re-checks the IP; that lives in the §4.5 fetch
layer, which calls this on every hop.)
"""

from __future__ import annotations

import ipaddress
from collections.abc import Collection

from bizup_autocrawler.urls import host_on_domain as is_on_domain
from bizup_autocrawler.urls import normalize_host

__all__ = ["SsrfError", "check_url", "is_on_domain"]

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
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_url(url: str, allowed_hosts: Collection[str]) -> None:
    """Raise :class:`SsrfError` unless ``url``'s host is on-domain and external."""
    host = normalize_host(url)
    if not host:
        raise SsrfError(f"no host in url: {url!r}")
    if host in _BLOCKED_HOSTNAMES or _is_blocked_ip(host):
        raise SsrfError(f"blocked internal target: {host}")
    if not is_on_domain(host, allowed_hosts):
        raise SsrfError(f"off-domain host {host!r} not in {sorted(allowed_hosts)}")
