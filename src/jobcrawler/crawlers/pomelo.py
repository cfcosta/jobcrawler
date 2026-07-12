
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/careerspomelo/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    # Greenhouse entity-escapes the full HTML body.
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting["absolute_url"],
            "apply_url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Pomelo",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
            "source": SITE,
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
            fields["department"] = ", ".join(departments)

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
