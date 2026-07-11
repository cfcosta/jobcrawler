SITE = "https://suse.wd3.myworkdayjobs.com/wday/cxs/suse/Jobsatsuse/jobs"
EXTRA_HOSTS = []

import html
import json
import re

CXS_BASE = "https://suse.wd3.myworkdayjobs.com/wday/cxs/suse/Jobsatsuse"
PAGE_SIZE = 20
MAX_PAGES = 25


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    seen = set()
    for page in range(MAX_PAGES):
        body = ctx.fetch(
            SITE,
            method="POST",
            json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
        )
        postings = json.loads(body).get("jobPostings") or []
        if not postings:
            break
        for posting in postings:
            path = posting.get("externalPath")
            if not path or path in seen:
                continue
            seen.add(path)
            detail = json.loads(ctx.fetch(CXS_BASE + path))
            info = detail.get("jobPostingInfo") or {}
            title = info.get("title") or posting.get("title")
            location = info.get("location") or posting.get("locationsText")
            description = _text_from_html(info.get("jobDescription"))
            url = info.get("externalUrl")
            if not (url and title and location and description):
                continue
            job = {
                "url": url,
                "title": title,
                "company": "SUSE",
                "location": location,
                "description": description,
                "external_id": info.get("jobReqId"),
                "apply_url": url,
                "source": SITE,
            }
            if info.get("timeType"):
                job["employment_type"] = info.get("timeType")
            if info.get("remoteType"):
                job["workplace_type"] = info.get("remoteType")
            if info.get("startDate"):
                job["posted_at"] = info.get("startDate")
            yield job
        if len(postings) < PAGE_SIZE:
            break
