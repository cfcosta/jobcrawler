import html
import json
import re

SITE = "https://unit21.zohorecruit.com/jobs/Careers"
EXTRA_HOSTS = []

_BASE = "https://unit21.zohorecruit.com"

# The Zoho Recruit career page server-renders the full job list into a hidden
# <input id="jobs" value="[...html-escaped JSON...]"> element, which a client-side
# widget later re-renders (no extra network call). We read that JSON directly.
_JOBS_INPUT_RE = re.compile(r'<input[^>]*\bid="jobs"[^>]*\bvalue="([^"]*)"', re.I)
_JOBS_INPUT_ALT_RE = re.compile(r'value="([^"]*)"\s+id="jobs"', re.I)


def _strip_html(value):
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _slugify(title):
    slug = re.sub(r"[^0-9A-Za-z]+", "-", title or "").strip("-")
    return slug or "job"


def _location(job):
    parts = []
    for key in ("City", "State", "Country"):
        value = job.get(key)
        if value and value not in parts:
            parts.append(value.strip())
    if parts:
        return ", ".join(parts)
    if job.get("Remote_Job"):
        return "Remote"
    return ""


def _extract_jobs(page):
    match = _JOBS_INPUT_RE.search(page) or _JOBS_INPUT_ALT_RE.search(page)
    if not match:
        return []
    try:
        data = json.loads(html.unescape(match.group(1)))
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def crawl(ctx):
    page = ctx.fetch(SITE)
    jobs = _extract_jobs(page)
    ctx.log("unit21: extracted", len(jobs), "job records")

    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("Publish") is False or job.get("Is_Locked") is True:
            continue

        job_id = job.get("id")
        title = (job.get("Posting_Title") or job.get("Job_Opening_Name") or "").strip()
        description = _strip_html(job.get("Job_Description"))
        location = _location(job)
        if not (job_id and title and description and location):
            continue

        url = "%s/jobs/Careers/%s/%s" % (_BASE, job_id, _slugify(title))

        out = {
            "url": url,
            "title": title,
            "company": "Unit21",
            "location": location,
            "description": description,
            "source": SITE,
        }

        out["external_id"] = str(job_id)
        out["apply_url"] = url + "/apply"
        out["locations"] = [location]

        remote = job.get("Remote_Job")
        if remote is True:
            out["workplace_type"] = "Remote"
        elif remote is False:
            out["workplace_type"] = "On-site"

        employment_type = job.get("Job_Type")
        if employment_type:
            out["employment_type"] = employment_type

        department = job.get("Industry")
        if department:
            out["department"] = department

        posted_at = job.get("Date_Opened")
        if posted_at:
            out["posted_at"] = posted_at

        yield out
