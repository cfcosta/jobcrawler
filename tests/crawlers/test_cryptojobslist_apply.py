"""cryptojobslist captures the external apply link from each job page.

The visible "Apply" CTA on a cryptojobslist job page is a JS ``<button>`` with
no href — the outbound ATS link (Workable, Greenhouse, Lever, ...) ships only
inside the Next.js data blob as ``props.pageProps.job.applicationLink``. The
crawler already fetches the page for its JSON-LD, so the link is a parse-only
capture: taken when it is an external http(s) target, omitted otherwise so a
transient miss never clobbers a previously stored value.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import cryptojobslist

SITEMAP_URL = "https://cryptojobslist.com/sitemap-jobs.xml"
JOB_URL = "https://cryptojobslist.com/jobs/senior-software-engineer-at-coinlist"


def _sitemap() -> str:
    return f"<urlset><url><loc>{JOB_URL}</loc></url></urlset>"


def _jsonld() -> str:
    posting = {
        "@type": "JobPosting",
        "title": "Senior Software Engineer",
        "description": "<p>Build trading infrastructure in Python and Rust.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "CoinList"},
        "jobLocation": {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressCountry": "United States"},
        },
        "url": JOB_URL,
    }
    return '<script type="application/ld+json">%s</script>' % json.dumps(posting)


def _next_data(application_link: object) -> str:
    blob = {"props": {"pageProps": {"job": {"applicationLink": application_link}}}}
    return (
        '<script id="__NEXT_DATA__" type="application/json">%s</script>'
        % json.dumps(blob)
    )


def _page(application_link: object, *, next_data: bool = True) -> str:
    data = _next_data(application_link) if next_data else ""
    return "<html><body>%s%s</body></html>" % (_jsonld(), data)


class _Ctx:
    def __init__(self, page: str) -> None:
        self.page = page

    def fetch(self, url: str) -> str:
        return _sitemap() if url == SITEMAP_URL else self.page


def _crawl_one(page: str) -> dict:
    jobs = list(cryptojobslist.crawl(_Ctx(page)))
    assert len(jobs) == 1
    return jobs[0]


def test_application_link_becomes_apply_url() -> None:
    href = "https://apply.workable.com/coinlist/j/FB4052F8B9/apply/"
    job = _crawl_one(_page(href))
    assert job["apply_url"] == href


def test_self_hosted_link_is_not_an_apply_url() -> None:
    # an in-house form on the aggregator is not a distinct per-employer target
    job = _crawl_one(_page("https://cryptojobslist.com/apply/coinlist-123"))
    assert "apply_url" not in job


def test_null_or_email_link_omits_apply_url() -> None:
    assert "apply_url" not in _crawl_one(_page(None))
    assert "apply_url" not in _crawl_one(_page("jobs@coinlist.co"))


def test_missing_next_data_blob_omits_apply_url() -> None:
    job = _crawl_one(_page(None, next_data=False))
    assert "apply_url" not in job
