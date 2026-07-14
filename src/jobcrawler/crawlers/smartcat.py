
import json
import re
from html import unescape

SITE = "https://boards-api.greenhouse.io/v1/boards/smartcatplatforminc/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|"
        r"blockquote|pre|address|header|footer|main|aside|dl|dt|dd|hr)"
        r"(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    raw = response.text if hasattr(response, "text") else response
    payload = json.loads(raw)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]

        job = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": item.get("company_name") or "Smartcat",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "locations": offices or None,
        }

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            value.get("currency_type")
            for value in ranges
            if value.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                value["min_cents"]
                for value in ranges
                if value.get("min_cents") is not None
            ]
            maximums = [
                value["max_cents"]
                for value in ranges
                if value.get("max_cents") is not None
            ]
            if minimums:
                job["salary_min"] = min(minimums) / 100
            if maximums:
                job["salary_max"] = max(maximums) / 100
            job["salary_currency"] = next(iter(currencies))

        yield {key: value for key, value in job.items() if value is not None}
