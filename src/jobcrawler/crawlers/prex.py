
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://prex.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location_text(job):
    loc = job.get("location") or {}
    parts = []
    city = loc.get("city")
    state = loc.get("state")
    if city and city != "*":
        parts.append(city)
    if state and state != "*" and state not in parts:
        parts.append(state)
    return ", ".join(parts) if parts else None


def crawl(ctx):
    listing = json.loads(ctx.fetch(SITE))
    for item in listing.get("result") or []:
        job_id = item.get("id")
        if not job_id:
            continue

        detail_url = urljoin(SITE, f"/careers/{job_id}/detail")
        detail = json.loads(ctx.fetch(detail_url))
        job = ((detail.get("result") or {}).get("jobOpening") or {})
        if job.get("jobOpeningStatus") and job.get("jobOpeningStatus") != "Open":
            continue

        title = job.get("jobOpeningName") or item.get("jobOpeningName")
        description = _strip_html(job.get("description"))
        location = _location_text(job) or _location_text(item)
        url = job.get("jobOpeningShareUrl") or urljoin(SITE, f"/careers/{job_id}")

        if not (title and description and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Prex",
            "location": location,
            "description": description,
            "external_id": str(job_id),
            "source": SITE,
        }

        department = job.get("departmentLabel") or item.get("departmentLabel")
        if department:
            out["department"] = department

        employment_type = job.get("employmentStatusLabel") or item.get("employmentStatusLabel")
        if employment_type:
            out["employment_type"] = employment_type

        posted_at = job.get("datePosted")
        if posted_at:
            out["posted_at"] = posted_at

        share_url = job.get("jobOpeningShareUrl")
        if share_url:
            out["apply_url"] = share_url

        yield out
