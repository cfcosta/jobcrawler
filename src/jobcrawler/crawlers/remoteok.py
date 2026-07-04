import json, re, html
from datetime import datetime, timezone

SITE = "https://remoteok.com/api"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = _TAG_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _epoch_to_iso(epoch):
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except Exception:
        return None


def crawl(ctx):
    raw = ctx.fetch("https://remoteok.com/api")
    data = json.loads(raw)
    # Skip element 0 — legal notice, not a job
    for job in data[1:]:
        if not isinstance(job, dict):
            continue
        title = (job.get("position") or "").strip()
        if not title:
            continue
        company = (job.get("company") or "").strip()
        desc_html = job.get("description") or ""
        description = _strip_html(desc_html)
        if not description:
            description = title
        location = (job.get("location") or "").strip()
        if not location:
            location = "Remote"
        job_url = (job.get("url") or "").strip()
        apply_url = (job.get("apply_url") or "").strip() or None
        external_id = str(job.get("id")) if job.get("id") else None
        posted_at = _epoch_to_iso(job.get("epoch"))
        tags = job.get("tags") or []
        department = tags[0] if tags else None
        # salary_min/salary_max are always 0 on RemoteOK — leave unset
        yield {
            "url": job_url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "posted_at": posted_at,
            "apply_url": apply_url,
            "external_id": external_id,
            "department": department,
            "source": SITE,
        }
