import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/apprentice/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(
        r"(?i)</?(?:p|div|li|ul|ol|h[1-6]|section|article|blockquote|pre|tr|table)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Apprentice",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "source": "greenhouse",
            "external_id": str(posting["id"]),
            "apply_url": posting["absolute_url"],
        }

        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
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
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield Job(**fields)
