
import json
import re
from html import unescape


SITE = "https://matera.inhire.app/vagas"
EXTRA_HOSTS = ["api.inhire.app"]

_API = "https://api.inhire.app/job-posts/public/pages"
_HEADERS = {"X-Tenant": "matera"}


def _decoded(response):
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        response = response.text
    if isinstance(response, (bytes, bytearray)):
        response = response.decode("utf-8")
    if isinstance(response, str):
        return json.loads(response)
    return response


def _records(value):
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []

    for key in ("jobsPage", "jobs", "items", "content", "results", "data"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            records = _records(candidate)
            if records:
                return records
    return []


def _detail(value):
    while isinstance(value, dict):
        nested = None
        for key in ("job", "jobPost", "jobPostPage", "data", "result"):
            candidate = value.get(key)
            if isinstance(candidate, dict):
                nested = candidate
                break
        if nested is None:
            return value
        value = nested
    return {}


def _plain_text(value):
    if not value:
        return None

    text = unescape(str(value))
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _scalar(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _first(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return _scalar(value)
    return None


def crawl(ctx):
    payload = _decoded(ctx.fetch(_API, headers=_HEADERS))

    for summary in _records(payload):
        if not isinstance(summary, dict):
            continue

        status = str(summary.get("status") or "").lower()
        if status and status != "published":
            continue

        job_id = _first(summary, "jobId", "id", "uuid")
        if not job_id:
            continue

        raw_detail = _decoded(
            ctx.fetch(f"{_API}/{job_id}", headers=_HEADERS)
        )
        detail = _detail(raw_detail)
        if not isinstance(detail, dict):
            continue

        detail_status = str(detail.get("status") or status).lower()
        if detail_status and detail_status != "published":
            continue

        title = _first(detail, "displayName", "title", "name")
        if not title:
            title = _first(summary, "displayName", "title", "name")

        description = _plain_text(
            _first(detail, "description", "descriptionHtml", "content")
        )

        location = _first(detail, "location", "locationName")
        if not location:
            location = _first(summary, "location", "locationName")
        if isinstance(location, dict):
            location = _first(location, "name", "displayName", "city")

        workplace = _first(detail, "workplaceType", "workplace_type")
        if not workplace:
            workplace = _first(summary, "workplaceType", "workplace_type")

        apply_url = _first(
            detail, "applyUrl", "applicationUrl", "apply_url", "applicationLink"
        )

        department = _first(detail, "department", "departmentName", "area")
        if isinstance(department, dict):
            department = _first(department, "name", "displayName")

        fields = {
            "url": f"{SITE}/{job_id}",
            "source": SITE,
            "title": title,
            "company": "Matera",
            "location": location,
            "description": description,
            "external_id": str(job_id),
            "apply_url": apply_url,
            "workplace_type": workplace,
            "department": department,
            "posted_at": _first(
                detail, "publishedAt", "postedAt", "publicationDate", "createdAt"
            ),
            "expires_at": _first(
                detail, "expiresAt", "expirationDate", "closingDate"
            ),
            "employment_type": _first(
                detail, "employmentType", "contractType", "commitment"
            ),
        }
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield fields
