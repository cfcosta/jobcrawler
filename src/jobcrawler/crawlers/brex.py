import html
import json
import re

SITE = "https://www.brex.com/careers"
EXTRA_HOSTS = []

MAX_JOBS = 8

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.I | re.S
)


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _next_data(page):
    match = _NEXT_DATA_RE.search(page or "")
    if not match:
        return None
    raw = match.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        try:
            return json.loads(html.unescape(raw))
        except ValueError:
            return None


def _page_props(data):
    props = data.get("props") if isinstance(data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    return page_props if isinstance(page_props, dict) else None


def _department(job):
    depts = job.get("departments")
    if isinstance(depts, list):
        for dept in depts:
            if isinstance(dept, dict):
                name = _text(dept.get("name"))
                if name and name.lower() != "no department":
                    return name
    meta = job.get("metadata")
    if isinstance(meta, list):
        for entry in meta:
            if isinstance(entry, dict) and entry.get("name") == "Job Post Department":
                value = _text(entry.get("value"))
                if value:
                    return value
    return None


def _workplace_type(job):
    names = []
    loc = job.get("location")
    if isinstance(loc, dict):
        name = _text(loc.get("name"))
        if name:
            names.append(name)
    offices = job.get("offices")
    if isinstance(offices, list):
        for office in offices:
            if isinstance(office, dict):
                name = _text(office.get("name"))
                if name:
                    names.append(name)
    if "remote" in " ".join(names).lower():
        return "Remote"
    return None


def _detail_url(job_id):
    return f"{SITE}/{job_id}"


def _fresh_ids(ctx):
    page_props = _page_props(_next_data(ctx.fetch(SITE)))
    jobs_data = page_props.get("jobsData") if page_props else None
    jobs = jobs_data.get("jobs") if isinstance(jobs_data, dict) else None
    if not isinstance(jobs, list):
        return []
    rows = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id")
        if job_id is None:
            continue
        rows.append((_text(job.get("updated_at")) or "", str(job_id)))
    rows.sort(reverse=True)
    ordered = []
    seen = set()
    for _, job_id in rows:
        if job_id not in seen:
            seen.add(job_id)
            ordered.append(job_id)
    return ordered


def crawl(ctx):
    for job_id in _fresh_ids(ctx)[:MAX_JOBS]:
        page_props = _page_props(_next_data(ctx.fetch(_detail_url(job_id))))
        job = page_props.get("job") if page_props else None
        if not isinstance(job, dict):
            continue

        title = _text(job.get("title"))
        description = _clean_html(job.get("content"))
        location = None
        loc = job.get("location")
        if isinstance(loc, dict):
            location = _text(loc.get("name"))
        if not (title and description and location):
            continue

        item = {
            "url": _detail_url(job_id),
            "title": title,
            "company": "Brex",
            "location": location,
            "description": description,
            "source": "brex",
            "external_id": job_id,
        }

        apply_url = _text(job.get("absolute_url"))
        if apply_url:
            item["apply_url"] = apply_url

        department = _department(job)
        if department:
            item["department"] = department

        posted_at = _text(job.get("first_published"))
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = _text(job.get("application_deadline"))
        if expires_at:
            item["expires_at"] = expires_at

        workplace_type = _workplace_type(job)
        if workplace_type:
            item["workplace_type"] = workplace_type

        yield item
