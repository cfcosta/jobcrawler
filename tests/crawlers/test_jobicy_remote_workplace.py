"""Jobicy's /api/v2/remote-jobs feed is remote-only, so the crawler marks every
listing remote. That turns each jobGeo region into a remote fence downstream
("USA" -> Remote (US)) instead of a physical place, and keeps the Anywhere
bucket worldwide. The crawler used to set workplace_type only when the literal
word "remote" appeared in jobGeo (it never does), and used a capitalized
"Remote" the §4.6 promotion never matched — so region roles read as physical
locations and the Anywhere bucket was dropped.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import jobicy_developer as crawler

_BODY = "<p>" + "Build and operate reliable backend services. " * 8 + "</p>"


class _Ctx:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def fetch(self, url: str) -> str:
        return self._payload


def _job(**over) -> dict:
    base = {
        "id": 1,
        "jobTitle": "Backend Engineer",
        "companyName": "Acme",
        "jobGeo": "USA",
        "url": "https://jobicy.com/jobs/1",
        "jobDescription": _BODY,
    }
    base.update(over)
    return base


def _crawl(jobs: list[dict]) -> dict[str, dict]:
    return {j["url"]: j for j in crawler.crawl(_Ctx(json.dumps({"jobs": jobs})))}


def test_geo_region_job_is_marked_remote() -> None:
    url = "https://jobicy.com/jobs/1"
    jobs = _crawl([_job(id=1, url=url, jobGeo="USA")])
    job = jobs[url]
    assert job["location"] == "USA"
    # Lowercase — the value the downstream §4.6 fence-promotion actually matches.
    assert job["workplace_type"] == "remote"


def test_anywhere_bucket_is_marked_remote() -> None:
    url = "https://jobicy.com/jobs/2"
    jobs = _crawl([_job(id=2, url=url, jobGeo="Anywhere")])
    job = jobs[url]
    assert job["location"] == "Anywhere"
    assert job["workplace_type"] == "remote"


def test_multi_region_geo_is_marked_remote() -> None:
    url = "https://jobicy.com/jobs/3"
    jobs = _crawl([_job(id=3, url=url, jobGeo="Europe,  Norway")])
    job = jobs[url]
    assert job["location"] == "Europe,  Norway"
    assert job["workplace_type"] == "remote"
