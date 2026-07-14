import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/extrahopnetworks/jobs?content=true"
EXTRA_HOSTS = []
API_URL = SITE + "&pay_transparency=true"


def _description(value):
    text = unescape(value or "")
    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _salary_fields(ranges):
    usable = [
        item for item in (ranges or [])
        if item.get("currency_type")
        and (item.get("min_cents") is not None
             or item.get("max_cents") is not None)
    ]
    currencies = {item["currency_type"] for item in usable}
    if not usable or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"] / 100
        for item in usable
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in usable
        if item.get("max_cents") is not None
    ]
    fields = {"salary_currency": usable[0]["currency_type"]}
    if minimums:
        fields["salary_min"] = min(minimums)
    if maximums:
        fields["salary_max"] = max(maximums)

    periods = {
        item.get("time_unit") or item.get("interval")
        for item in usable
        if item.get("time_unit") or item.get("interval")
    }
    if len(periods) == 1:
        fields["salary_period"] = periods.pop()
    return fields


def crawl(ctx):
    response = ctx.fetch(API_URL)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _description(posting.get("content"))
        url = posting.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title.strip(),
            "company": "ExtraHop",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        offices = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

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

        fields.update(_salary_fields(posting.get("pay_input_ranges")))
        yield fields
