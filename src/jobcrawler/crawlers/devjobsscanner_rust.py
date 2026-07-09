
import json
import re
from urllib.parse import urlencode

SITE = "https://www.devjobsscanner.com/api/searchFull/?search=rust&limit=200"
EXTRA_HOSTS = []

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = re.sub(r"\s+", " ", value).strip()
        return value or None
    return value

def _description(job):
    parts = []
    title = _clean(job.get("title"))
    company = _clean(job.get("company"))
    location = _clean(job.get("location") or job.get("city"))
    if title and company:
        parts.append(f"{title} at {company}.")
    elif title:
        parts.append(title)
    if location:
        parts.append(f"Location: {location}.")
    if job.get("remoteOk") is True:
        parts.append("Workplace: remote.")
    elif job.get("remoteOk") is False:
        parts.append("Workplace: onsite or location-based.")
    stacks = []
    for key in ("stackRequired", "stackOptional"):
        vals = job.get(key) or []
        if isinstance(vals, list):
            stacks.extend(str(v).strip() for v in vals if str(v).strip())
    if stacks:
        parts.append("Technologies: " + ", ".join(dict.fromkeys(stacks)) + ".")
    source = _clean(job.get("source"))
    if source:
        parts.append(f"Original source board: {source}.")
    apply_url = _clean(job.get("url"))
    if apply_url:
        parts.append(f"Original posting URL: {apply_url}")
    return "\n".join(parts) or None

def _job_url(job):
    ext = _clean(job.get("id") or job.get("_id") or job.get("url") or job.get("title")) or "job"
    return SITE + "&external_id=" + urlencode({"id": ext})[3:]

def crawl(ctx):
    page = 0
    seen = set()
    while True:
        sep = "&" if "?" in SITE else "?"
        url = SITE if page == 0 else SITE + sep + "page=" + str(page)
        data = json.loads(ctx.fetch(url))
        jobs = data.get("jobs") or []
        if not jobs:
            break

        for item in jobs:
            title = _clean(item.get("title"))
            company = _clean(item.get("company"))
            location = _clean(item.get("location") or item.get("city"))
            desc = _description(item)
            if not (title and company and location and desc):
                continue

            external_id = _clean(item.get("id") or item.get("_id"))
            dedupe = external_id or _clean(item.get("url")) or title
            if dedupe in seen:
                continue
            seen.add(dedupe)

            job = {
                "url": _job_url(item),
                "title": title,
                "company": company,
                "location": location,
                "description": desc,
                "source": SITE,
            }

            apply_url = _clean(item.get("url"))
            if apply_url:
                job["apply_url"] = apply_url
            if external_id:
                job["external_id"] = external_id

            posted_at = _clean(item.get("postedDate") or item.get("date"))
            if posted_at:
                job["posted_at"] = posted_at

            if item.get("remoteOk") is True:
                job["workplace_type"] = "remote"
            elif item.get("remoteOk") is False:
                job["workplace_type"] = "onsite"

            board = _clean(item.get("source"))
            if board:
                job["department"] = board

            cur = _clean(item.get("salaryCurrency"))
            minv = item.get("salaryMin")
            maxv = item.get("salaryMax")
            if cur and isinstance(minv, (int, float)) and isinstance(maxv, (int, float)) and minv >= 0 and maxv >= 0:
                if minv == maxv:
                    job["salary"] = f"{cur} {int(minv) if float(minv).is_integer() else minv}"
                else:
                    lo = int(minv) if float(minv).is_integer() else minv
                    hi = int(maxv) if float(maxv).is_integer() else maxv
                    job["salary"] = f"{cur} {lo}-{hi}"

            yield job

        page += 1
