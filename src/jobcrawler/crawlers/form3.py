
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/form3/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    decoded = html.unescape(value or "")
    without_tags = re.sub(r"<[^>]*>", " ", decoded)
    return " ".join(html.unescape(without_tags).split())


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs", []):
        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]

        fields = {
            "source": SITE,
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Form3",
            "location": item["location"]["name"],
            "description": _plain_text(item["content"]),
            "external_id": str(item["id"]),
        }

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]
        if departments:
            fields["department"] = ", ".join(departments)
        if offices:
            fields["locations"] = offices

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
