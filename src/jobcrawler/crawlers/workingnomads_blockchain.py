
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://www.workingnomads.com/remote-blockchain-jobs"
EXTRA_HOSTS = []

DISCOVERY_URL = "https://www.workingnomads.com/jobsapi/_search?q=blockchain&size=1000&sort=pub_date:desc"

def _clean_text(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol|section|tr)>", "\n", value)
    value = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None

def _position_type(value):
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

def _location(src):
    locs = src.get("locations")
    if isinstance(locs, list):
        text = ", ".join(str(item).strip() for item in locs if str(item).strip())
        if text:
            return text
    for key in ("location_base", "location_extra"):
        val = src.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return "Remote"

def _put(job, key, value):
    if value is not None and str(value).strip():
        job[key] = str(value).strip()

def crawl(ctx):
    data = json.loads(ctx.fetch(DISCOVERY_URL))
    seen = set()

    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source") or {}
        if src.get("expired") is True:
            continue

        slug = src.get("slug")
        if not slug:
            continue
        url = urljoin(SITE, "/jobs/" + str(slug).strip("/"))
        if url in seen:
            continue
        seen.add(url)

        description = _clean_text(src.get("description"))
        title = _clean_text(src.get("title"))
        company = _clean_text(src.get("company"))
        if not (title and company and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": _location(src),
            "description": description,
            "source": SITE,
        }

        _put(job, "employment_type", _position_type(src.get("position_type")))
        _put(job, "department", src.get("category_name"))
        _put(job, "posted_at", src.get("pub_date"))
        _put(job, "external_id", src.get("external_id"))
        _put(job, "apply_url", src.get("apply_url"))
        salary = src.get("salary_range") or src.get("salary_range_short")
        _put(job, "salary", salary)

        yield job
