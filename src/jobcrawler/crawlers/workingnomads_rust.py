
import re
import json
import html
from urllib.parse import quote, urljoin

SITE = "https://www.workingnomads.com/remote-rust-jobs"
EXTRA_HOSTS = []

API_URL = "https://www.workingnomads.com/jobsapi/_search"

def _clean_text(value):
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|ul|ol|section|article)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip() or None

def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
        if value not in ("", [], {}):
            return value
    return None

def _location(src):
    locations = src.get("locations")
    if isinstance(locations, list) and locations:
        vals = [_clean_text(x) for x in locations if _clean_text(x)]
        if vals:
            return ", ".join(vals)
    return _first_nonempty(
        _clean_text(src.get("location_base")),
        _clean_text(src.get("location_extra")),
        "Remote",
    )

def _salary(src):
    salary = _first_nonempty(
        src.get("salary_range"),
        src.get("salary_range_short"),
        src.get("annual_salary_usd"),
    )
    if salary is None:
        return None
    if isinstance(salary, (int, float)):
        return str(salary)
    return _clean_text(salary)

def _canonical_url(src):
    slug = src.get("slug")
    if slug:
        return "https://www.workingnomads.com/jobs/" + str(slug).strip("/")
    job_id = src.get("id") or src.get("external_id")
    if job_id:
        return "https://www.workingnomads.com/jobs/" + str(job_id)
    return None

def _apply_url(src):
    value = _first_nonempty(src.get("apply_url"), src.get("url"))
    if not value:
        return None
    return urljoin(SITE, str(value))

def _to_job(src):
    if src.get("expired") is True:
        return None

    tags = src.get("tags") or []
    tag_text = " ".join(str(t).lower() for t in tags)
    if "rust" not in tag_text:
        return None

    title = _clean_text(src.get("title"))
    company = _clean_text(src.get("company"))
    description = _clean_text(src.get("description"))
    url = _canonical_url(src)
    location = _location(src)

    if not (url and title and company and location and description):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    employment_type = _clean_text(src.get("position_type"))
    if employment_type:
        job["employment_type"] = employment_type

    job["workplace_type"] = "Remote"

    department = _clean_text(src.get("category_name"))
    if department:
        job["department"] = department

    salary = _salary(src)
    if salary:
        job["salary"] = salary

    external_id = _first_nonempty(src.get("external_id"), src.get("id"))
    if external_id is not None:
        job["external_id"] = str(external_id)

    apply = _apply_url(src)
    if apply:
        job["apply_url"] = apply

    # pub_date is relative in this source, so it is intentionally not emitted as posted_at.
    return job

def _search_url(offset, size):
    payload = {
        "from": offset,
        "size": size,
        "query": {"match": {"tags": "rust"}},
        "sort": [
            {"pub_date": {"order": "desc"}},
            {"id": {"order": "desc"}},
        ],
    }
    return API_URL + "?source=" + quote(json.dumps(payload, separators=(",", ":"))) + "&source_content_type=application/json"

def crawl(ctx):
    seen = set()
    page_size = 50
    offset = 0

    while True:
        data = json.loads(ctx.fetch(_search_url(offset, page_size)))
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            src = hit.get("_source") or {}
            job = _to_job(src)
            if not job or job["url"] in seen:
                continue
            seen.add(job["url"])
            yield job

        if len(hits) < page_size:
            break
        offset += page_size
