
import html
import json
import re
from urllib.parse import quote, urljoin

SITE = "https://www.workingnomads.com/jobs?category=development"
EXTRA_HOSTS = []

API_URL = "https://www.workingnomads.com/jobsapi/_search"


def _strip_tags(value):
    if not value:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _join_texts(values):
    parts = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text = item.get("name") or item.get("title") or item.get("location")
                else:
                    text = item
                if text and str(text).strip() not in parts:
                    parts.append(str(text).strip())
        elif isinstance(value, dict):
            text = value.get("name") or value.get("title") or value.get("location")
            if text and str(text).strip() not in parts:
                parts.append(str(text).strip())
        else:
            text = str(value).strip()
            if text and text not in parts:
                parts.append(text)
    return ", ".join(parts) if parts else None


def _salary(source):
    for key in ("salary_range", "salary_range_short", "annual_salary_usd"):
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _job_url(source):
    slug = source.get("slug")
    if slug:
        return urljoin(SITE, "/jobs/" + str(slug).strip("/"))
    job_id = source.get("id") or source.get("external_id")
    if job_id:
        return urljoin(SITE, "/jobs/" + str(job_id))
    return None


def _apply_url(source):
    value = source.get("apply_url")
    if value:
        return urljoin(SITE, str(value))
    job_id = source.get("id")
    if job_id:
        return urljoin(SITE, "/job/go/%s/" % job_id)
    return None


def _api_page_url(offset, size):
    query = {
        "from": offset,
        "size": size,
        "sort": [{"pub_date": {"order": "desc"}}],
        "query": {"match": {"category_name": "Development"}},
    }
    return API_URL + "?source=" + quote(json.dumps(query)) + "&source_content_type=application/json"


def crawl(ctx):
    size = 100
    offset = 0
    seen = set()

    while True:
        data = json.loads(ctx.fetch(_api_page_url(offset, size)))
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            break

        for hit in hits:
            source = hit.get("_source", {})
            if source.get("expired") is True:
                continue
            if str(source.get("category_name") or "").lower() != "development":
                continue

            url = _job_url(source)
            title = source.get("title")
            company = source.get("company")
            description = _strip_tags(source.get("description"))
            location = _join_texts([source.get("locations"), source.get("location_base"), source.get("location_extra")]) or "Remote"

            if not (url and title and company and description and location):
                continue
            if url in seen:
                continue
            seen.add(url)

            job = {
                "url": url,
                "title": str(title).strip(),
                "company": str(company).strip(),
                "location": location,
                "description": description,
                "source": SITE,
            }

            optional = {
                "department": source.get("category_name"),
                "employment_type": source.get("position_type"),
                "posted_at": source.get("pub_date"),
                "external_id": source.get("external_id") or source.get("id"),
                "apply_url": _apply_url(source),
                "salary": _salary(source),
            }
            for key, value in optional.items():
                if value not in (None, ""):
                    job[key] = str(value).strip()

            yield job

        if len(hits) < size:
            break
        offset += size
