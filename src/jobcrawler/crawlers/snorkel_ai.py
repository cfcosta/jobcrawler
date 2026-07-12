
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/snorkelai/jobs?content=true"
EXTRA_HOSTS = []


def _text(markup):
    value = html.unescape(markup or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/tr)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        kwargs = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Snorkel AI",
            "location": location,
            "description": _text(item.get("content")),
            "external_id": str(item["id"]),
            "apply_url": item["absolute_url"],
        }

        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = ", ".join(departments)

        if item.get("first_published"):
            kwargs["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            kwargs["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay.get("currency_type")
            for pay in ranges
            if pay.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay["min_cents"] / 100
                for pay in ranges
                if pay.get("min_cents") is not None
            ]
            maximums = [
                pay["max_cents"] / 100
                for pay in ranges
                if pay.get("max_cents") is not None
            ]
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))
            kwargs["salary_period"] = "1 YEAR"

        kwargs["source"] = SITE
        yield kwargs
