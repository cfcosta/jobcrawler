"""euremotejobs fences each posting by its job_listing_region taxonomy.

The board is a WP Job Manager site whose only authoritative geography is the
``job_listing_region`` term(s) attached to each listing ("Worldwide", "Europe",
"Germany", ...). The crawler used to discard it and stamp a bare ``"Remote"`` on
every job, so a Europe-only role and a genuinely worldwide one were
indistinguishable downstream — both drifted to "Remote (Worldwide)" or to
nothing at all. It now resolves the region id->name map once and turns each
posting's ids into a geo-parseable ``"Remote - <region>"`` string.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import eu_remote_jobs as crawler

# id -> (name) for the taxonomy fixture; ids mirror the live board's shape.
REGIONS = {
    73: "Worldwide",
    90: "Europe",
    437: "Germany",
    408: "Poland",
}


def _region_page(page: int) -> str:
    # One page holds the whole (tiny) fixture; later pages are empty, which is
    # how the crawler's pagination loop terminates.
    if page != 1:
        return "[]"
    return json.dumps([{"id": tid, "name": name} for tid, name in REGIONS.items()])


def _listing(slug: str, region_ids: list[int]) -> dict:
    return {
        "id": abs(hash(slug)) % 100000,
        "status": "publish",
        "link": f"https://euremotejobs.com/job/{slug}",
        "title": {"rendered": slug.replace("-", " ").title()},
        "content": {"rendered": "<p>Build things remotely.</p>"},
        "meta": {"_company_name": "Acme", "_remote_position": 0},
        "job_listing_region": region_ids,
    }


class _Ctx:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def fetch(self, url: str) -> str:
        # The taxonomy endpoint and the listings endpoint are told apart the same
        # way the crawler builds them: only the taxonomy URL lacks "job-listings".
        if "job_listing_region" in url and "job-listings" not in url:
            page = int(dict(_q(url)).get("page", "1"))
            return _region_page(page)
        page = int(dict(_q(url)).get("page", "1"))
        return json.dumps(self._items) if page == 1 else "[]"


def _q(url: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl, urlparse

    return parse_qsl(urlparse(url).query)


def _crawl(items: list[dict]) -> dict[str, dict]:
    return {j["url"]: j for j in crawler.crawl(_Ctx(items))}


def test_worldwide_region_becomes_worldwide_location() -> None:
    jobs = _crawl([_listing("eng-manager", [73])])
    job = jobs["https://euremotejobs.com/job/eng-manager"]
    assert job["location"] == "Remote - Worldwide"
    assert job["workplace_type"] == "remote"


def test_europe_region_is_fenced_not_worldwide() -> None:
    jobs = _crawl([_listing("fwd-deployed", [90])])
    assert jobs["https://euremotejobs.com/job/fwd-deployed"]["location"] == "Remote - Europe"


def test_multiple_regions_join_into_one_fence_string() -> None:
    jobs = _crawl([_listing("multi", [437, 408])])
    # Order follows the posting's id list so the parser emits one fence apiece.
    assert jobs["https://euremotejobs.com/job/multi"]["location"] == "Remote - Germany, Poland"


def test_absent_or_unknown_region_falls_back_to_bare_remote() -> None:
    # No ids at all, and an id the taxonomy fetch never resolved, both degrade to
    # a bare "Remote" rather than dropping the posting.
    jobs = _crawl([_listing("no-region", []), _listing("stale-id", [99999])])
    assert jobs["https://euremotejobs.com/job/no-region"]["location"] == "Remote"
    assert jobs["https://euremotejobs.com/job/stale-id"]["location"] == "Remote"


def test_region_fetch_failure_degrades_to_remote() -> None:
    class _Failing(_Ctx):
        def fetch(self, url: str) -> str:
            if "job_listing_region" in url and "job-listings" not in url:
                raise RuntimeError("taxonomy endpoint down")
            return super().fetch(url)

    jobs = {j["url"]: j for j in crawler.crawl(_Failing([_listing("eng-manager", [73])]))}
    assert jobs["https://euremotejobs.com/job/eng-manager"]["location"] == "Remote"
