import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/dfinity/jobs?content=true"
EXTRA_HOSTS = []
API_URL = SITE + "&pay_transparency=true"
COMPANY = "DFINITY"


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:address|article|aside|"
        r"blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|nav|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)"
        r"(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _names(items):
    return [
        item["name"].strip()
        for item in (items or [])
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].strip()
    ]


def _salary_fields(item):
    ranges = item.get("pay_input_ranges") or []
    valid = [
        value
        for value in ranges
        if isinstance(value, dict)
        and value.get("currency_type")
        and value.get("min_cents") is not None
        and value.get("max_cents") is not None
    ]
    currencies = {value["currency_type"] for value in valid}
    if not valid or len(currencies) != 1:
        return {}

    periods = {
        value.get("interval")
        for value in valid
        if value.get("interval")
    }
    result = {
        "salary_min": min(value["min_cents"] for value in valid) / 100,
        "salary_max": max(value["max_cents"] for value in valid) / 100,
        "salary_currency": next(iter(currencies)),
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(API_URL))

    for item in payload.get("jobs", []):
        departments = _names(item.get("departments"))
        offices = _names(item.get("offices"))
        kwargs = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": COMPANY,
            "location": (item.get("location") or {}).get("name"),
            "description": _description(item.get("content")),
            "source": "greenhouse",
            "external_id": str(item["id"]),
        }
        if departments:
            kwargs["department"] = ", ".join(departments)
        if offices:
            kwargs["locations"] = offices
        kwargs.update(_salary_fields(item))
        yield Job(**kwargs)
