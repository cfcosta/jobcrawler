
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        url = posting.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        values = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": posting.get("company_name") or "Elastic",
            "location": location,
            "description": description,
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }

        offices = [
            office.get("name")
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            values["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            values["department"] = ", ".join(departments)

        ranges = posting.get("pay_input_ranges") or []
        complete_ranges = [
            item
            for item in ranges
            if item.get("min_cents") is not None
            and item.get("max_cents") is not None
            and item.get("currency_type")
        ]
        currencies = {
            item.get("currency_type")
            for item in complete_ranges
        }
        if complete_ranges and len(currencies) == 1:
            values["salary_min"] = min(
                item["min_cents"] for item in complete_ranges
            ) / 100
            values["salary_max"] = max(
                item["max_cents"] for item in complete_ranges
            ) / 100
            values["salary_currency"] = next(iter(currencies))

        yield Job(
            **{
                key: value
                for key, value in values.items()
                if value is not None and value != ""
            }
        )
