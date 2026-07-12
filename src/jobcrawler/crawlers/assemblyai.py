
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/assemblyai/jobs?content=true"
EXTRA_HOSTS = []

_API_URL = SITE + "&pay_transparency=true"


def _plain_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?is)<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        " ",
        text,
    )
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table|hr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(_API_URL))

    for posting in payload.get("jobs", []):
        title = (posting.get("title") or "").strip()
        location = ((posting.get("location") or {}).get("name") or "").strip()
        description = _plain_text(posting.get("content"))
        url = posting.get("absolute_url")

        if not title or not location or not description or not url:
            continue

        kwargs = {
            "url": url,
            "title": title,
            "company": "AssemblyAI",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
        }

        office_names = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if office_names:
            kwargs["locations"] = office_names

        department_names = [
            department["name"].strip()
            for department in posting.get("departments", [])
            if department.get("name") and department["name"].strip()
        ]
        if department_names:
            kwargs["department"] = ", ".join(department_names)

        if posting.get("first_published"):
            kwargs["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            kwargs["expires_at"] = posting["application_deadline"]

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
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
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        kwargs["source"] = SITE
        yield kwargs
