
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/pagerduty/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        canonical_url = posting.get("absolute_url")

        if not title or not location or not description or not canonical_url:
            continue

        fields = {
            "url": canonical_url,
            "source": SITE,
            "title": title,
            "company": posting.get("company_name") or "PagerDuty",
            "location": location,
            "description": description,
            "external_id": str(posting.get("id")),
            "apply_url": canonical_url,
        }

        offices = [
            office.get("name")
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if posting.get("first_published"):
            fields["posted_at"] = posting.get("first_published")
        if posting.get("application_deadline"):
            fields["expires_at"] = posting.get("application_deadline")

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                item.get("min_cents") / 100
                for item in ranges
                if item.get("min_cents") is not None
            ]
            maximums = [
                item.get("max_cents") / 100
                for item in ranges
                if item.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = list(currencies)[0]

        yield fields
