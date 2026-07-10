"""remote3 crawls both listing surfaces: the main feed and /top-web3-job-list.

The top list is a 200+-page archive index whose first pages surface postings
that never appear on the main feed (user-reported gap). The crawler walks a
bounded freshness window of it — page depth is pinned here so a regen or edit
that drops the second surface fails loudly.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import remote3


def _job_record(slug: str, title: str) -> dict:
    return {
        "slug": slug,
        "title": title,
        "location": "Remote",
        "companies": {"name": "Acme Web3"},
        "description": "<p>Build distributed systems in Rust.</p>",
        "id": slug,
        "salary_min": None,
        "salary_max": None,
        "live_at": "2026-07-08T00:00:00Z",
    }


def _page_html(jobs: list[dict]) -> str:
    chunk = '"initialJobs":' + json.dumps(jobs) + ',"totalPages":1'
    return "<script>self.__next_f.push(%s)</script>" % json.dumps([1, chunk])


class _Ctx:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        return self.pages.get(url, _page_html([]))


def test_crawl_walks_the_top_list_window_and_dedupes_across_surfaces() -> None:
    main = [_job_record("shared-role", "Shared Role"), _job_record("main-only", "Main Only")]
    top1 = [_job_record("shared-role", "Shared Role"), _job_record("top-only", "Top Only")]
    ctx = _Ctx(
        {
            "https://www.remote3.co/": _page_html(main),
            "https://www.remote3.co/top-web3-job-list": _page_html(top1),
        }
    )

    jobs = list(remote3.crawl(ctx))
    urls = [j["url"] for j in jobs]

    assert "https://www.remote3.co/remote-jobs/main-only" in urls
    assert "https://www.remote3.co/remote-jobs/top-only" in urls
    # A posting listed on both surfaces yields exactly once.
    assert urls.count("https://www.remote3.co/remote-jobs/shared-role") == 1

    top_fetches = [u for u in ctx.fetched if "top-web3-job-list" in u]
    assert top_fetches[0] == "https://www.remote3.co/top-web3-job-list"
    # Bounded window: every page of the window is visited, and only the window.
    assert len(top_fetches) == remote3.TOP_LIST_PAGES
    assert top_fetches[1:] == [
        "https://www.remote3.co/top-web3-job-list?page=%d" % n
        for n in range(2, remote3.TOP_LIST_PAGES + 1)
    ]


def test_top_list_window_is_a_bounded_freshness_window() -> None:
    # The archive runs 200+ pages; the crawler must not walk it all.
    assert 2 <= remote3.TOP_LIST_PAGES <= 10
