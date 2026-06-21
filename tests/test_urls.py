"""Tests for URL canonicalization (§4.6).

One shared runtime helper, used by the dedup key (§4.7), the gate-3 Jaccard
(§6), and — via its host-only primitive — the SSRF on-domain check (§8). The
load-bearing property: two URLs differing only by a tracking param must
canonicalize equal, or a correct crawler gets rejected by gate-3.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from bizup_autocrawler.urls import canonicalize_url, normalize_host


def test_lowercases_scheme_and_host() -> None:
    assert canonicalize_url("HTTPS://Example.COM/Path") == "https://example.com/Path"


def test_preserves_path_case() -> None:
    # paths are case-sensitive; only scheme + host are lowercased.
    assert (
        canonicalize_url("https://example.com/Foo/Bar") == "https://example.com/Foo/Bar"
    )


def test_strips_default_ports() -> None:
    assert canonicalize_url("http://example.com:80/x") == "http://example.com/x"
    assert canonicalize_url("https://example.com:443/x") == "https://example.com/x"


def test_keeps_nondefault_port() -> None:
    assert (
        canonicalize_url("https://example.com:8443/x") == "https://example.com:8443/x"
    )


def test_drops_fragment() -> None:
    assert canonicalize_url("https://example.com/x#section") == "https://example.com/x"


def test_root_path_normalized_to_slash() -> None:
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_strips_trailing_slash_on_nonroot() -> None:
    assert canonicalize_url("https://example.com/foo/") == "https://example.com/foo"


def test_uppercases_percent_encoding() -> None:
    assert canonicalize_url("https://example.com/a%2fb") == "https://example.com/a%2Fb"


def test_drops_utm_and_known_tracking_params() -> None:
    url = "https://example.com/a?utm_source=x&utm_medium=y&fbclid=z&gclid=w"
    assert canonicalize_url(url) == "https://example.com/a"


def test_keeps_and_sorts_meaningful_params() -> None:
    assert (
        canonicalize_url("https://example.com/a?b=2&a=1")
        == "https://example.com/a?a=1&b=2"
    )


def test_tracking_only_query_dropped_entirely() -> None:
    assert (
        canonicalize_url("https://example.com/a?utm_source=x")
        == "https://example.com/a"
    )


def test_url_differing_only_by_tracking_param_canonicalizes_equal() -> None:
    # the load-bearing case for gate-3 / dedup (§4.6).
    with_tracking = canonicalize_url("https://example.com/story?utm_source=newsletter")
    without = canonicalize_url("https://example.com/story")
    assert with_tracking == without


def test_custom_drop_params_replaces_default_but_utm_still_dropped() -> None:
    out = canonicalize_url(
        "https://example.com/a?ref=twitter&id=5&utm_x=1", drop_params={"ref"}
    )
    assert out == "https://example.com/a?id=5"


def test_normalize_host_lowercases_and_strips_port() -> None:
    assert normalize_host("HTTPS://News.Example.COM:443/path") == "news.example.com"


def test_ipv6_literal_host_keeps_brackets_and_round_trips() -> None:
    # an IPv6-literal host must stay bracketed so the canonical form is a valid,
    # re-parseable URL (the dedup key / gate-3 Jaccard depend on round-tripping).
    out = canonicalize_url("https://[::1]:8443/x")
    assert out == "https://[::1]:8443/x"
    assert urlsplit(out).port == 8443  # re-parses without ValueError


def test_ipv6_literal_default_port_stripped() -> None:
    assert canonicalize_url("https://[2001:db8::1]:443/x") == "https://[2001:db8::1]/x"


def test_normalize_host_returns_bracketless_ipv6() -> None:
    # the SSRF primitive feeds ipaddress.ip_address(), which wants no brackets.
    assert normalize_host("https://[::1]:8443/x") == "::1"
