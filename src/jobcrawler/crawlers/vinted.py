
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/vinteden/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", text)
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table|/section|/article)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _metadata(job):
    result = {}
    for item in job.get("metadata") or []:
        name = item.get("name")
        value = item.get("value")
        if name and value is not None and value != "":
            result[name] = value
    return result


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {item.get("currency_type") for item in ranges}
    if len(ranges) != 1 or len(currencies) != 1 or None in currencies:
        return {}

    item = ranges[0]
    minimum = item.get("min_cents")
    maximum = item.get("max_cents")
    if minimum is None and maximum is None:
        return {}

    title = item.get("title") or ""
    lower_title = title.lower()
    if "annual" in lower_title:
        period = "annual"
    elif "monthly" in lower_title:
        period = "monthly"
    elif "hourly" in lower_title:
        period = "hourly"
    else:
        return {}

    fields = {
        "salary_currency": item["currency_type"],
        "salary_period": period,
    }
    if minimum is not None:
        fields["salary_min"] = minimum / 100
    if maximum is not None:
        fields["salary_max"] = maximum / 100
    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for item in data.get("jobs") or []:
        metadata = _metadata(item)
        kwargs = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Vinted",
            "location": (item.get("location") or {}).get("name"),
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": item.get("absolute_url"),
            "posted_at": item.get("first_published"),
            "employment_type": metadata.get("Employment Type"),
            "department": metadata.get("Department for Careers Web Posting"),
        }

        locations = [
            office.get("name")
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        if locations:
            kwargs["locations"] = locations

        kwargs.update(_salary_fields(item))
        yield {key: value for key, value in kwargs.items() if value is not None}
