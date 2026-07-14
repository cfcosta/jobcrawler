import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/bishopfox/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs", []):
        kwargs = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Bishop Fox",
            "location": item["location"]["name"],
            "description": _description(item["content"]),
            "external_id": str(item["id"]),
            "source": SITE,
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = " / ".join(departments)

        if item.get("first_published"):
            kwargs["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            kwargs["expires_at"] = item["application_deadline"]

        pay_ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in pay_ranges
            if pay_range.get("currency_type")
        }
        if pay_ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in pay_ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in pay_ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        yield kwargs
