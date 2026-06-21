"""Tests for the SSRF guard (§8).

ctx restricts requests to the site's registered host(s) and on-domain links,
and blocks private/loopback/link-local IP ranges + cloud-metadata endpoints,
so model-written crawler code can't reach internal services.
"""

from __future__ import annotations

import pytest

from bizup_autocrawler.runtime.ssrf import SsrfError, check_url, is_on_domain


def test_on_domain_exact_and_subdomain() -> None:
    assert is_on_domain("example.com", {"example.com"})
    assert is_on_domain("news.example.com", {"example.com"})


def test_off_domain_and_suffix_attack_rejected() -> None:
    assert not is_on_domain("evil.com", {"example.com"})
    assert not is_on_domain("example.com.evil.com", {"example.com"})
    assert not is_on_domain("notexample.com", {"example.com"})


def test_check_url_allows_on_domain() -> None:
    check_url("https://news.example.com/story", {"example.com"})  # no raise


def test_check_url_rejects_off_domain() -> None:
    with pytest.raises(SsrfError):
        check_url("https://evil.com/x", {"example.com"})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://[::1]/x",
        "http://metadata.google.internal/x",
    ],
)
def test_check_url_blocks_internal_targets(url: str) -> None:
    # even if the operator "allowed" the literal host, internal targets are blocked.
    with pytest.raises(SsrfError):
        check_url(
            url,
            {
                "127.0.0.1",
                "10.0.0.1",
                "192.168.1.1",
                "169.254.169.254",
                "::1",
                "metadata.google.internal",
            },
        )


def test_check_url_requires_a_host() -> None:
    with pytest.raises(SsrfError):
        check_url("file:///etc/passwd", {"example.com"})
