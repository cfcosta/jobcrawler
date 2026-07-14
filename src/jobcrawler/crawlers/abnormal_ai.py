
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/abnormalsecurity/jobs?content=true"
EXTRA_HOSTS = []
_DISCOVERY_URL = SITE + "&pay_transparency=true"


def _description(raw_html):
    text = html.unescape(raw_html)
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    valid = [
        item for item in ranges
        if item.get("min_cents") is not None
        and item.get("max_cents") is not None
        and item.get("currency_type")
    ]
    if not valid or len(valid) != len(ranges) or len(currencies) != 1:
        return {}

    return {
        "salary_min": min(item["min_cents"] for item in valid) / 100,
        "salary_max": max(item["max_cents"] for item in valid) / 100,
        "salary_currency": next(iter(currencies)),
    }


def crawl(ctx):
    payload = json.loads(ctx.fetch(_DISCOVERY_URL))
    jobs = payload.get("jobs") or []
    total = (payload.get("meta") or {}).get("total")
    if total is not None and total != len(jobs):
        raise ValueError(
            "Greenhouse response was incomplete: expected %s jobs, received %s"
            % (total, len(jobs))
        )

    for item in jobs:
        location = (item.get("location") or {}).get("name")
        office_names = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        department_names = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "Abnormal AI",
            "location": location,
            "description": _description(item.get("content") or ""),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "department": ", ".join(department_names) if department_names else None,
        }
        if office_names:
            fields["locations"] = office_names
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]
        fields.update(_salary_fields(item))

        yield {key: value for key, value in fields.items() if value is not None}
