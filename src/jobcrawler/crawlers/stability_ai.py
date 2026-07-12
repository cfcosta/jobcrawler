
import json
import re
from html import unescape
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://boards-api.greenhouse.io/v1/boards/stabilityai/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/blockquote|/tr)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _api_url():
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["content"] = "true"
    query["pay_transparency"] = "true"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def crawl(ctx):
    response = ctx.fetch(_api_url())
    payload = json.loads(response)

    for item in payload.get("jobs", []):
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
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Stability AI",
            "location": (item.get("location") or {}).get("name"),
            "locations": offices or None,
            "description": _plain_text(item.get("content")),
            "source": "greenhouse",
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "apply_url": item.get("absolute_url"),
            "department": ", ".join(departments) if departments else None,
        }

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            value.get("currency_type")
            for value in ranges
            if value.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                value["min_cents"]
                for value in ranges
                if value.get("min_cents") is not None
            ]
            maximums = [
                value["max_cents"]
                for value in ranges
                if value.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums) / 100
            if maximums:
                fields["salary_max"] = max(maximums) / 100
            fields["salary_currency"] = next(iter(currencies))

        yield {key: value for key, value in fields.items() if value is not None}
