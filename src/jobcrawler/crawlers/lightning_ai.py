
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/lightningai/jobs?content=true"
EXTRA_HOSTS = []

DISCOVERY_URL = SITE + "&pay_transparency=true"


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:p|div|li|h[1-6]|"
        r"section|article|blockquote|pre|tr|td|th|ul|ol)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _salary_fields(ranges):
    if not ranges:
        return {}

    currencies = {item.get("currency_type") for item in ranges}
    if None in currencies or len(currencies) != 1:
        return {}

    minimums = [item.get("min_cents") for item in ranges]
    maximums = [item.get("max_cents") for item in ranges]
    if any(value is None for value in minimums + maximums):
        return {}

    result = {
        "salary_min": min(minimums) / 100,
        "salary_max": max(maximums) / 100,
        "salary_currency": next(iter(currencies)),
    }

    titles = " ".join(item.get("title") or "" for item in ranges)
    if re.search(r"\bannual\b", titles, re.IGNORECASE):
        result["salary_period"] = "annual"
    elif re.search(r"\bhourly\b", titles, re.IGNORECASE):
        result["salary_period"] = "hourly"

    return result


def crawl(ctx):
    response = ctx.fetch(DISCOVERY_URL)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Lightning AI",
            "location": location,
            "description": _description(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }

        locations = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        department_names = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]
        if department_names:
            fields["department"] = ", ".join(department_names)

        fields.update(_salary_fields(item.get("pay_input_ranges") or []))
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield fields
