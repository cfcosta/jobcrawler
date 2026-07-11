"""PostHog states the hiring region in a Timezone custom field as
"<Region> (GMT band)". A global region word bounded by a timezone band —
"Anywhere (GMT +2 to GMT -8)" — is not worldwide (the band excludes
Asia/Pacific), yet the bare "Anywhere" made every such role project
"Remote (Worldwide)". The crawler now demotes the leading global token to
"Remote" so geo reads it as a plain remote marker, while real regions
("Americas (GMT -3 to GMT -8)", "UK (GMT 0)") are left untouched.
"""

from __future__ import annotations

from jobcrawler.crawlers import posthog
from jobcrawler.runtime import geo


def _posting(tz_value: str, key: str = "Timezone") -> dict:
    return {"parent": {"customFields": [{"title": key, "value": tz_value}]}}


def test_global_token_with_tz_band_is_demoted() -> None:
    out = posthog._location(_posting("Anywhere (GMT +2 to GMT -8)"))
    assert out == "Remote (GMT +2 to GMT -8)"
    # And the whole point: geo no longer reads it as worldwide.
    assert geo.parse_location(out).worldwide is False


def test_real_region_with_tz_band_is_untouched() -> None:
    assert posthog._location(_posting("Americas (GMT -3 to GMT -8)")) == (
        "Americas (GMT -3 to GMT -8)"
    )
    assert posthog._location(_posting("UK (GMT 0)")) == "UK (GMT 0)"


def test_bare_global_without_band_stays_worldwide() -> None:
    # No timezone band means genuinely worldwide — leave it for geo to read.
    out = posthog._location(_posting("Anywhere"))
    assert out == "Anywhere"
    assert geo.parse_location(out).worldwide is True


def test_list_valued_timezone_demotes_global_member() -> None:
    out = posthog._location(
        _posting('["Anywhere (GMT +2 to GMT -8)"]', key="Timezone(s)")
    )
    assert out == "Remote (GMT +2 to GMT -8)"


def test_missing_timezone_falls_back_to_remote() -> None:
    assert posthog._location({"parent": {"customFields": []}}) == "Remote"
