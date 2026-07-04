"""Crawler for GitLab Greenhouse job board."""

import json, html, re

SITE = "https://boards-api.greenhouse.io/v1/boards/gitlab/jobs?content=true"
COMPANY = "GitLab"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html_text: str) -> str:
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """
    Yield one Job dict per open role from the Greenhouse boards API.
    """
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        desc = _strip_tags(job.get("content", ""))

        depts = [d.get("name") for d in job.get("departments", []) if d.get("name")]
        department = "; ".join(depts) if depts else None

        loc = job.get("location", {})
        location = loc.get("name") if loc else None

        first_published = job.get("first_published")
        updated_at = job.get("updated_at")
        application_deadline = job.get("application_deadline")

        company = job.get("company_name") or COMPANY

        yield {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": company,
            "location": location,
            "description": desc,
            "source": "greenhouse_api",
            "external_id": str(job.get("id")) if job.get("id") else None,
            "apply_url": job.get("absolute_url"),
            "posted_at": first_published,
            "updated_at": updated_at,
            "expires_at": application_deadline,
            "department": department,
            "employment_type": None,
            "workplace_type": None,
            "salary": None,
        }
