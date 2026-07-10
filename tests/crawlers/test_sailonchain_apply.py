"""sailonchain captures the external apply link from each job page.

Every sailonchain job page carries a "Visit job opening page" CTA
(`<a class="job-outbound-cta" href="<external ATS url>">`) pointing at the
company's real posting — same pattern as WWR's "Apply now" button. The crawler
already fetches the page for its JSON-LD, so the href is a parse-only capture:
taken when it is an external http(s) target, omitted otherwise so a transient
miss never clobbers a previously stored value.
"""

from __future__ import annotations

import json

from jobcrawler.crawlers import sailonchain

SITEMAP_URL = "https://sailonchain.com/sitemaps/jobs.xml"
JOB_URL = "https://sailonchain.com/jobs/backend-engineer-acme"


def _sitemap() -> str:
    return f"<urlset><url><loc>{JOB_URL}</loc></url></urlset>"


def _jsonld() -> str:
    posting = {
        "@type": "JobPosting",
        "title": "Backend Engineer",
        "description": "<p>Build Rust services for chain infrastructure.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Chain"},
        "jobLocation": {
            "@type": "Place",
            "address": {"@type": "PostalAddress", "addressCountry": "Remote"},
        },
        "url": JOB_URL,
    }
    return (
        '<script type="application/ld+json">%s</script>' % json.dumps(posting)
    )


def _page(cta_href: str | None) -> str:
    cta = ""
    if cta_href is not None:
        cta = (
            '<a class="job-outbound-cta flex flex-row" href="%s" target="_blank" '
            'rel="noopener noreferrer">Visit job opening page</a>' % cta_href
        )
    return "<html><body>%s%s</body></html>" % (_jsonld(), cta)


class _Ctx:
    def __init__(self, page: str) -> None:
        self.page = page

    def fetch(self, url: str) -> str:
        return _sitemap() if url == SITEMAP_URL else self.page


def _crawl_one(page: str) -> dict:
    jobs = list(sailonchain.crawl(_Ctx(page)))
    assert len(jobs) == 1
    return jobs[0]


def test_outbound_cta_becomes_apply_url() -> None:
    href = "https://jobs.ashbyhq.com/tempo-xyz/8bba7722?ref=sailonchain.com"
    job = _crawl_one(_page(href))
    assert job["apply_url"] == href


def test_same_host_cta_is_not_an_apply_url() -> None:
    job = _crawl_one(_page("https://sailonchain.com/out/backend-engineer-acme"))
    assert "apply_url" not in job


def test_missing_cta_omits_apply_url() -> None:
    job = _crawl_one(_page(None))
    assert "apply_url" not in job


def test_entity_escaped_href_is_unescaped() -> None:
    job = _crawl_one(_page("https://boards.greenhouse.io/acme/jobs/1?a=1&amp;b=2"))
    assert job["apply_url"] == "https://boards.greenhouse.io/acme/jobs/1?a=1&b=2"
