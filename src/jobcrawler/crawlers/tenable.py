
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/tenableinc/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _payload(response):
    if isinstance(response, str):
        return json.loads(response)
    if isinstance(response, bytes):
        return json.loads(response.decode("utf-8"))
    if isinstance(response, dict):
        return response
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        text = response.text
        return json.loads(text() if callable(text) else text)
    raise TypeError("Unsupported fetch response")


def crawl(ctx):
    data = _payload(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        if not item.get("title") or not location or not description:
            continue

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Tenable, Inc.",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        departments = [
            entry["name"]
            for entry in (item.get("departments") or [])
            if entry.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        offices = [
            entry["name"]
            for entry in (item.get("offices") or [])
            if entry.get("name")
        ]
        if offices:
            fields["locations"] = offices

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            entry.get("currency_type")
            for entry in ranges
            if entry.get("currency_type")
        }
        if len(ranges) == 1 and len(currencies) == 1:
            pay = ranges[0]
            if pay.get("min_cents") is not None:
                fields["salary_min"] = pay["min_cents"] / 100
            if pay.get("max_cents") is not None:
                fields["salary_max"] = pay["max_cents"] / 100
            fields["salary_currency"] = pay["currency_type"]

        yield fields
