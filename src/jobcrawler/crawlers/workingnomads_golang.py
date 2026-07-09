
import html
import json
import re

SITE = "https://www.workingnomads.com/remote-golang-jobs"
EXTRA_HOSTS = []

API_URL = "https://www.workingnomads.com/jobsapi/_search?q=golang&size=500&sort=pub_date:desc"


def _clean_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _location(src):
    parts = []
    for key in ("locations", "location_base", "location_extra"):
        value = src.get(key)
        if isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
        elif value:
            parts.append(str(value).strip())
    seen = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or "Remote"


def _employment_type(value):
    mapping = {
        "ft": "Full-time",
        "pt": "Part-time",
        "contract": "Contract",
        "freelance": "Freelance",
        "internship": "Internship",
    }
    if not value:
        return None
    return mapping.get(str(value).lower(), str(value))


def _job_kwargs(src):
    kwargs = {
        "url": "https://www.workingnomads.com/jobs/" + str(src.get("slug") or src.get("id")),
        "title": str(src.get("title") or "").strip(),
        "company": str(src.get("company") or "").strip(),
        "location": _location(src),
        "description": _clean_html(src.get("description")),
        "source": SITE,
    }

    optional = {
        "department": src.get("category_name"),
        "posted_at": src.get("pub_date"),
        "external_id": src.get("external_id") or src.get("id"),
        "apply_url": src.get("apply_url"),
        "salary": src.get("salary_range") or src.get("salary_range_short"),
        "employment_type": _employment_type(src.get("position_type")),
    }
    for key, value in optional.items():
        if value is not None and str(value).strip():
            kwargs[key] = str(value).strip() if key == "external_id" else value
    return kwargs


def crawl(ctx):
    data = json.loads(ctx.fetch(API_URL))
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source") or {}
        if src.get("expired") is True:
            continue
        kwargs = _job_kwargs(src)
        if kwargs["title"] and kwargs["company"] and kwargs["description"]:
            yield kwargs
