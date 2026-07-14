import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/bloomreach/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", " ", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _employment_type(metadata):
    for item in metadata or []:
        if item.get("name") == "Employment Type":
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _salary_fields(ranges):
    currencies = {
        item.get("currency_type")
        for item in ranges or []
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"] / 100
        for item in ranges
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in ranges
        if item.get("max_cents") is not None
    ]

    fields = {"salary_currency": next(iter(currencies))}
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        title = item.get("title")
        posting_url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content") or "")
        if not title or not posting_url or not location or not description:
            continue

        fields = {
            "url": posting_url,
            "title": title.strip(),
            "company": "Bloomreach",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        employment_type = _employment_type(item.get("metadata"))
        if employment_type:
            fields["employment_type"] = employment_type

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        fields.update(_salary_fields(item.get("pay_input_ranges")))
        yield fields
