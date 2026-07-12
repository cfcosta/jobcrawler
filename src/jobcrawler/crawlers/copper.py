import json
import re
from html import unescape


SITE = 'https://boards-api.greenhouse.io/v1/boards/copperco/jobs?content=true'
EXTRA_HOSTS = []


def _plain_text(value):
    # Greenhouse content is entity-escaped HTML, sometimes more than once.
    text = unescape(value or "")
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]*>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    separator = "&" if "?" in SITE else "?"
    data = json.loads(ctx.fetch(
        SITE + separator + "pay_transparency=true"
    ))

    for item in data.get("jobs", []):
        job_url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        job_id = item.get("id")

        locations = [
            office.get("name")
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": job_url,
            "title": (item.get("title") or "").strip(),
            "company": "Copper.co",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": "greenhouse",
            "posted_at": item.get("first_published"),
            "external_id": str(job_id) if job_id is not None else None,
            "apply_url": job_url,
        }

        if locations:
            fields["locations"] = locations
        if departments:
            fields["department"] = ", ".join(departments)
        if item.get("application_deadline"):
            fields["expires_at"] = item.get("application_deadline")

        ranges = item.get("pay_input_ranges") or []
        currencies = set(
            entry.get("currency_type")
            for entry in ranges
            if entry.get("currency_type")
        )
        if ranges and len(currencies) == 1:
            minimums = [
                entry.get("min_cents") / 100
                for entry in ranges
                if entry.get("min_cents") is not None
            ]
            maximums = [
                entry.get("max_cents") / 100
                for entry in ranges
                if entry.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = list(currencies)[0]

        yield fields
