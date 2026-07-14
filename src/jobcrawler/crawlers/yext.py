
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/yext/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(br|p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|table|tr)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _department(job):
    names = [
        item.get("name")
        for item in (job.get("departments") or [])
        if item.get("name")
    ]
    return " / ".join(names) if names else None


def _salary_fields(job):
    ranges = [
        item
        for item in (job.get("pay_input_ranges") or [])
        if item.get("currency_type")
        and item.get("min_cents") is not None
        and item.get("max_cents") is not None
    ]
    currencies = {item["currency_type"] for item in ranges}
    if not ranges or len(currencies) != 1:
        return {}

    titles = " ".join(
        item.get("title") or "" for item in ranges
    ).lower()
    if "annual" in titles or "year" in titles:
        period = "annual"
    elif "hour" in titles:
        period = "hourly"
    elif "month" in titles:
        period = "monthly"
    elif "week" in titles:
        period = "weekly"
    else:
        return {}

    return {
        "salary_min": min(item["min_cents"] for item in ranges) / 100,
        "salary_max": max(item["max_cents"] for item in ranges) / 100,
        "salary_currency": next(iter(currencies)),
        "salary_period": period,
    }


def crawl(ctx):
    endpoint = SITE + "&pay_transparency=true"
    payload = json.loads(ctx.fetch(endpoint))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in (item.get("offices") or [])
            if office.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Yext",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": _department(item),
        }
        if offices:
            fields["locations"] = offices
        fields.update(_salary_fields(item))

        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield fields
