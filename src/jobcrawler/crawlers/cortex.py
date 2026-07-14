
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/cortex/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _add_compensation(fields, item):
    ranges = [
        value for value in item.get("pay_input_ranges", [])
        if value.get("currency_type")
        and (value.get("min_cents") is not None or value.get("max_cents") is not None)
    ]
    currencies = {value["currency_type"] for value in ranges}
    if not ranges or len(currencies) != 1:
        return

    minimums = [
        value["min_cents"] / 100
        for value in ranges
        if value.get("min_cents") is not None
    ]
    maximums = [
        value["max_cents"] / 100
        for value in ranges
        if value.get("max_cents") is not None
    ]
    periods = {
        value.get("interval") or value.get("pay_period")
        for value in ranges
        if value.get("interval") or value.get("pay_period")
    }

    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)
    fields["salary_currency"] = next(iter(currencies))
    if len(periods) == 1:
        fields["salary_period"] = next(iter(periods))


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]

        fields = {
            "source": SITE,
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "Cortex",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]),
        }
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        _add_compensation(fields, item)
        yield fields
