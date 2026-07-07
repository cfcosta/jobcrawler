
import json
import re
import html

SITE = "https://www.coinbase.com/careers/positions"
EXTRA_HOSTS = []

LISTING_URL = "https://www.coinbase.com/api/v2/careers"
DETAIL_BASE = "https://www.coinbase.com/api/v2/careers/"
MAX_DETAIL_FETCHES = 24

def _present(value):
    return value not in (None, "", [], {})

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(td|th)\s*>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _location(job):
    loc = job.get("location") or {}
    if isinstance(loc, dict) and _present(loc.get("name")):
        return str(loc["name"]).strip()
    return None

def _departments(job, fallback=None):
    names = []
    for dep in job.get("departments") or []:
        if isinstance(dep, dict) and _present(dep.get("name")):
            names.append(str(dep["name"]).strip())
    if not names and fallback:
        names.append(str(fallback).strip())
    unique = []
    for name in names:
        if name and name not in unique:
            unique.append(name)
    return ", ".join(unique) or None

def crawl(ctx):
    listing = json.loads(ctx.fetch(LISTING_URL))
    departments = (listing.get("data") or {}).get("departments") or []

    seen = set()
    fetched = 0
    for dep in departments:
        dep_name = dep.get("name") if isinstance(dep, dict) else None
        for summary in (dep.get("jobs") or []):
            job_id = summary.get("id")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            if fetched >= MAX_DETAIL_FETCHES:
                return
            fetched += 1

            detail = json.loads(ctx.fetch(f"{DETAIL_BASE}{job_id}"))
            job = detail.get("data") or {}
            title = (job.get("title") or summary.get("title") or "").strip()
            description = _clean_html(job.get("content"))
            location = _location(job) or _location(summary)
            if not (title and description and location):
                continue

            item = {
                "url": f"https://www.coinbase.com/careers/positions/{job_id}",
                "title": title,
                "company": "Coinbase",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": f"https://job-boards.greenhouse.io/embed/job_app?token={job_id}&for=coinbase",
            }

            department = _departments(job, dep_name)
            if department:
                item["department"] = department

            yield item
