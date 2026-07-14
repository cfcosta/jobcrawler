
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/huntress/jobs?content=true'
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value)
    text = re.sub(
        r"<(script|style)\\b[^>]*>.*?</\\1\\s*>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<\\s*br\\s*/?\\s*>|</?\\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)\\b[^>]*>",
        "\\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\\n".join(lines)


def crawl(ctx):
    api_url = SITE + ("&" if "?" in SITE else "?") + "pay_transparency=true"
    data = json.loads(ctx.fetch(api_url))

    for item in data.get("jobs", []):
        job = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Huntress",
            "location": item["location"]["name"],
            "description": _description(item["content"]),
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = ", ".join(departments)

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                job["salary_min"] = min(minimums)
            if maximums:
                job["salary_max"] = max(maximums)
            job["salary_currency"] = next(iter(currencies))

        yield job
