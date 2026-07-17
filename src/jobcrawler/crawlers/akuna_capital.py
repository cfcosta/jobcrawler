import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/akunacapital/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</\s*(?:address|article|aside|blockquote|div|"
        r"h[1-6]|li|ol|p|pre|section|table|tr|ul)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _metadata(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name:
            value = item.get("value")
            if value not in (None, "", []):
                if isinstance(value, list):
                    return ", ".join(str(part) for part in value)
                return str(value)
    return None


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

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

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    periods = {
        item.get("interval")
        for item in ranges
        if item.get("interval")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs") or []:
        location = (item.get("location") or {}).get("name")
        office_names = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": item.get("company_name") or "Akuna Capital",
            "location": location,
            "description": _description(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "employment_type": _metadata(item, "Employment Type"),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }
        if office_names:
            fields["locations"] = office_names
        fields.update(_salary_fields(item))

        yield Job(**{key: value for key, value in fields.items() if value is not None})
