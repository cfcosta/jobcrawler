import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/labelbox/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    url = SITE + "&pay_transparency=true"
    response = ctx.fetch(url)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [office.get("name") for office in item.get("offices", []) if office.get("name")]
        departments = [department.get("name") for department in item.get("departments", []) if department.get("name")]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "apply_url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Labelbox",
            "location": location,
            "description": _text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
            "locations": offices or None,
        }

        ranges = item.get("pay_input_ranges") or []
        currencies = {entry.get("currency_type") for entry in ranges if entry.get("currency_type")}
        if ranges and len(currencies) == 1:
            mins = [entry["min_cents"] for entry in ranges if entry.get("min_cents") is not None]
            maxes = [entry["max_cents"] for entry in ranges if entry.get("max_cents") is not None]
            if mins:
                fields["salary_min"] = min(mins) / 100
            if maxes:
                fields["salary_max"] = max(maxes) / 100
            fields["salary_currency"] = next(iter(currencies))
            titles = [entry.get("title", "") for entry in ranges]
            fields["salary_period"] = "hourly" if any("hour" in title.lower() for title in titles) else "1 YEAR"

        yield {key: value for key, value in fields.items() if value is not None}
