import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/hubspotjobs/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:p|div|h[1-6]|li|ul|ol|section|article|tr|table|blockquote)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _metadata(job):
    values = {}
    for item in job.get("metadata") or []:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None:
            values[name] = value
    return values


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0:
        return None
    if number.is_integer():
        return int(number)
    return number


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        metadata = _metadata(item)
        departments = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]
        offices = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "HubSpot",
            "location": (item.get("location") or {}).get("name"),
            "description": _description(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": item.get("absolute_url"),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "employment_type": metadata.get("Employment Type"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }

        compensation = metadata.get("Compensation Range (Job Posts)")
        if isinstance(compensation, dict) and compensation.get("unit"):
            salary_min = _number(compensation.get("min_value"))
            salary_max = _number(compensation.get("max_value"))
            if salary_min is not None or salary_max is not None:
                fields["salary_min"] = salary_min
                fields["salary_max"] = salary_max
                fields["salary_currency"] = compensation.get("unit")

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
