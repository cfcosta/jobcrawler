
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/highnote/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        departments = [
            item.get("name")
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        offices = [
            item.get("name")
            for item in posting.get("offices", [])
            if item.get("name")
        ]

        fields = {
            "url": posting.get("absolute_url"),
            "source": "greenhouse",
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Highnote",
            "location": location,
            "description": _description(posting.get("content")),
            "external_id": str(posting.get("id")),
            "posted_at": posting.get("first_published"),
        }

        if departments:
            fields["department"] = ", ".join(departments)
        if offices:
            fields["locations"] = offices
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
            fields["salary_currency"] = next(iter(currencies))

        yield fields
