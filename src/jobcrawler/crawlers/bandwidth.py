
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/bandwidth/jobs?content=true"
EXTRA_HOSTS = []

_DISCOVERY_URL = SITE + "&pay_transparency=true"


def _clean_html(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|"
        r"blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|"
        r"li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)"
        r"\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    blank = False
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            lines.append(line)
            blank = False
        elif lines and not blank:
            lines.append("")
            blank = True
    return "\n".join(lines).strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(_DISCOVERY_URL))

    for posting in payload.get("jobs", []):
        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Bandwidth",
            "location": (posting.get("location") or {}).get("name"),
            "description": _clean_html(posting.get("content")),
            "source": "greenhouse",
        }

        locations = [
            office.get("name")
            for office in posting.get("offices") or []
            if isinstance(office, dict) and office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if isinstance(department, dict) and department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if posting.get("requisition_id"):
            fields["external_id"] = str(posting["requisition_id"])
        elif posting.get("id") is not None:
            fields["external_id"] = str(posting["id"])

        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

        ranges = [
            item
            for item in posting.get("pay_input_ranges") or []
            if isinstance(item, dict)
            and item.get("min_cents") is not None
            and item.get("max_cents") is not None
            and item.get("currency_type")
        ]
        currencies = {item["currency_type"] for item in ranges}
        if ranges and len(currencies) == 1:
            fields["salary_min"] = min(item["min_cents"] for item in ranges) / 100
            fields["salary_max"] = max(item["max_cents"] for item in ranges) / 100
            fields["salary_currency"] = next(iter(currencies))

        yield fields
