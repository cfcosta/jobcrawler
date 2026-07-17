
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/nearform/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(?:p|div|li|h[1-6]|ul|ol|section|article)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _metadata(item, name):
    for entry in item.get("metadata") or []:
        if entry.get("name") == name:
            return entry.get("value")
    return None


def _single_value(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _salary_fields(item):
    ranges = item.get("pay_input_ranges") or []
    currencies = {
        value.get("currency_type")
        for value in ranges
        if value.get("currency_type")
    }
    if not ranges or len(currencies) > 1:
        return {}

    minimums = [
        value["min_cents"] / 100
        for value in ranges
        if value.get("min_cents") is not None
    ]
    maximums = [
        value["max_cents"] / 100
        for value in ranges
        if value.get("max_cents") is not None
    ]
    result = {}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)
    if len(currencies) == 1:
        result["salary_currency"] = next(iter(currencies))

    periods = {
        value.get("interval")
        for value in ranges
        if value.get("interval")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs") or []:
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content"))
        title = item.get("title")
        url = item.get("absolute_url")
        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Nearform",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        office_names = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if office_names:
            fields["locations"] = office_names

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        employment_type = _single_value(_metadata(item, "Employment Type"))
        workplace_type = _single_value(_metadata(item, "Work Type"))
        if employment_type:
            fields["employment_type"] = employment_type
        if workplace_type:
            fields["workplace_type"] = workplace_type
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        fields.update(_salary_fields(item))
        yield Job(**fields)
