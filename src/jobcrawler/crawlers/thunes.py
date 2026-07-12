
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/thunes/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
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
        fields = {
            "url": posting["absolute_url"],
            "source": SITE,
            "title": posting["title"],
            "company": "Thunes",
            "location": (posting.get("location") or {}).get("name"),
            "description": _plain_text(posting.get("content")),
            "external_id": str(posting["id"]),
            "apply_url": posting["absolute_url"],
        }

        locations = [
            office["location"]
            for office in posting.get("offices") or []
            if office.get("location")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = "; ".join(departments)

        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

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
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
