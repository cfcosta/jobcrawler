
import html
import json
import re
from jobcrawler.runtime.contract import Job


SITE = "https://apply.workable.com/api/v1/widget/accounts/risingwave-labs?details=true"
EXTRA_HOSTS = []


def _clean(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


def _html_text(value):
    if not isinstance(value, str) or not value.strip():
        return None

    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|<\s*/\s*(?:div|h[1-6]|li|ol|p|pre|section|table|tr|ul)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def _location_value(value):
    if isinstance(value, str):
        return _clean(value)
    if not isinstance(value, dict):
        return None

    parts = []
    for key in ("city", "region", "country"):
        part = _clean(value.get(key))
        if part and part not in parts:
            parts.append(part)

    if parts:
        return ", ".join(parts)
    if value.get("telecommuting") is True:
        return "Remote"
    return None


def _locations(item):
    values = item.get("locations")
    if not isinstance(values, list):
        return None

    result = []
    for value in values:
        if isinstance(value, str) and value.strip():
            result.append(value)
    return result or None


def _description(item):
    sections = []
    for key in ("description", "requirements", "benefits"):
        section = _html_text(item.get(key))
        if section:
            sections.append(section)
    return "\n\n".join(sections) or None


def crawl(ctx):
    body = ctx.fetch(SITE)
    payload = json.loads(body)

    company = _clean(payload.get("name")) or "RisingWave Labs"
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return

    for item in jobs:
        if not isinstance(item, dict):
            continue

        shortcode = _clean(item.get("shortcode"))
        title = _clean(item.get("title"))
        description = _description(item)
        location_data = item.get("location")
        location = _location_value(location_data)

        if not shortcode or not title or not description or not location:
            continue

        fields = {
            "url": f"https://apply.workable.com/risingwave-labs/j/{shortcode}/",
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "department": _clean(item.get("department")),
            "employment_type": _clean(item.get("employment_type")),
            "posted_at": _clean(item.get("created_at")),
            "external_id": _clean(item.get("code")) or shortcode,
            "apply_url": _clean(item.get("application_url")),
        }

        locations = _locations(item)
        if locations:
            fields["locations"] = locations

        if isinstance(location_data, dict) and location_data.get("telecommuting") is True:
            fields["workplace_type"] = "remote"

        yield Job(**{
            key: value
            for key, value in fields.items()
            if value is not None
        })
