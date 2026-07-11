"""Crawler for CrowdStrike's Workday careers board (crowdstrikecareers tenant)."""

import html
import json
import re

SITE = "https://crowdstrike.wd5.myworkdayjobs.com/wday/cxs/crowdstrike/crowdstrikecareers/jobs"
EXTRA_HOSTS = []

COMPANY = "CrowdStrike"
# CXS base for per-job detail lookups: strip the trailing "/jobs" from SITE.
CXS_BASE = SITE.rsplit("/jobs", 1)[0]
PAGE_SIZE = 20
# Mega-board (400+ roles): yield a bounded rolling sample of real, current jobs.
MAX_JOBS = 40


def _clean_text(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip() or None


def crawl(ctx):
    """
    Yield one Job dict per open role from CrowdStrike's Workday CXS board.

    The list endpoint (POST /jobs) paginates 20 at a time and only reports the
    real `total` on the first (offset 0) page, so capture it once. Each posting
    carries an `externalPath`; fetch CXS_BASE + externalPath for the full
    jobPostingInfo (title, location, HTML jobDescription, jobReqId).
    """
    total = None
    seen = set()
    yielded = 0
    offset = 0

    while yielded < MAX_JOBS:
        raw = ctx.fetch(
            SITE,
            method="POST",
            json={"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset},
        )
        page = json.loads(raw)
        if total is None:
            total = page.get("total") or 0
        postings = page.get("jobPostings") or []
        if not postings:
            break

        for post in postings:
            if yielded >= MAX_JOBS:
                break
            external_path = post.get("externalPath")
            if not external_path or external_path in seen:
                continue
            seen.add(external_path)

            detail = json.loads(ctx.fetch(CXS_BASE + external_path))
            info = detail.get("jobPostingInfo") or {}

            title = info.get("title")
            location = info.get("location")
            description = _clean_text(info.get("jobDescription"))
            if not (title and location and description):
                continue

            external_url = info.get("externalUrl")
            job = {
                "url": external_url or (CXS_BASE + external_path),
                "title": title,
                "company": COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
            }
            if info.get("jobReqId"):
                job["external_id"] = info["jobReqId"]
            if external_url:
                job["apply_url"] = external_url
            additional = info.get("additionalLocations")
            if additional:
                job["locations"] = [location] + [a for a in additional if a]
            if info.get("timeType"):
                job["employment_type"] = info["timeType"]
            if info.get("remoteType"):
                job["workplace_type"] = info["remoteType"]
            if info.get("startDate"):
                job["posted_at"] = info["startDate"]
            if info.get("endDate"):
                job["expires_at"] = info["endDate"]

            yielded += 1
            yield job

        offset += PAGE_SIZE
        if total and offset >= total:
            break
