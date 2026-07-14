
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/amplitude/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        text,
    )
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
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
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content"))
        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Amplitude",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if item.get("id") is not None:
            fields["external_id"] = str(item["id"])
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        offices = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        for metadata in item.get("metadata") or []:
            if (
                metadata.get("name") == "Employment Type"
                and metadata.get("value")
            ):
                fields["employment_type"] = metadata["value"]
                break

        pay_ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay["currency_type"]
            for pay in pay_ranges
            if pay.get("currency_type")
        }
        if pay_ranges and len(currencies) == 1:
            minimums = [
                pay["min_cents"] / 100
                for pay in pay_ranges
                if pay.get("min_cents") is not None
            ]
            maximums = [
                pay["max_cents"] / 100
                for pay in pay_ranges
                if pay.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
