import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/typeform/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\b[^>]*>|<\s*/?\s*(?:address|article|aside|"
        r"blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        kwargs = {
            "url": item["absolute_url"],
            "title": item["title"].strip(),
            "company": item.get("company_name") or "Typeform",
            "location": item["location"]["name"],
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]),
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
            kwargs["department"] = ", ".join(departments)

        if item.get("first_published"):
            kwargs["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            kwargs["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            entry.get("currency_type")
            for entry in ranges
            if entry.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                entry["min_cents"] / 100
                for entry in ranges
                if entry.get("min_cents") is not None
            ]
            maximums = [
                entry["max_cents"] / 100
                for entry in ranges
                if entry.get("max_cents") is not None
            ]
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        yield kwargs
