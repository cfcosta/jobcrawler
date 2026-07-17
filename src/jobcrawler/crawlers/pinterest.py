import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/pinterest/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name:
            value = item.get("value")
            if isinstance(value, list):
                values = [str(part) for part in value if part]
                return ", ".join(values) if values else None
            if value is not None:
                return str(value)
    return None


def _compensation(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"]
        for item in ranges
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"]
        for item in ranges
        if item.get("max_cents") is not None
    ]

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums) / 100
    if maximums:
        result["salary_max"] = max(maximums) / 100
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))

        if (
            not item.get("title")
            or not item.get("absolute_url")
            or not location
            or not description
        ):
            continue

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Pinterest",
            "location": location,
            "description": description,
            "external_id": str(item["id"]),
            "source": SITE,
        }

        office_names = [
            office.get("name")
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if office_names:
            fields["locations"] = office_names

        department_names = [
            department.get("name")
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if department_names:
            fields["department"] = ", ".join(department_names)

        employment_type = _metadata_value(item, "Employment Type")
        if employment_type:
            fields["employment_type"] = employment_type

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        fields.update(_compensation(item))
        yield Job(**fields)
