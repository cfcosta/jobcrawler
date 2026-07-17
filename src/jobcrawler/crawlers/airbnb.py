
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>|</(?:p|div|li|ul|ol|h[1-6]|tr|section)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _metadata_value(posting, name):
    for item in posting.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return item["value"]
    return None


def _compensation(posting):
    ranges = posting.get("pay_input_ranges") or []
    currencies = set()
    for item in ranges:
        if item.get("currency_type"):
            currencies.add(item["currency_type"])
    if not ranges or len(currencies) != 1:
        return {}

    minimums = []
    maximums = []
    for item in ranges:
        if item.get("min_cents") is not None:
            minimums.append(item["min_cents"] / 100)
        if item.get("max_cents") is not None:
            maximums.append(item["max_cents"] / 100)
    if not minimums and not maximums:
        return {}

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    titles = [item.get("title", "") for item in ranges]
    if titles and all("annual" in title.lower() for title in titles):
        result["salary_period"] = "Annual"
    elif titles and all("monthly" in title.lower() for title in titles):
        result["salary_period"] = "Monthly"
    elif titles and all("hour" in title.lower() for title in titles):
        result["salary_period"] = "Hourly"
    return result


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    for posting in payload.get("jobs") or []:
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Airbnb",
            "location": location,
            "description": description,
            "source": SITE,
        }

        office_names = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if office_names:
            fields["locations"] = office_names

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        workplace_type = _metadata_value(posting, "Workplace Type")
        if workplace_type:
            fields["workplace_type"] = workplace_type
        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]
        if posting.get("id") is not None:
            fields["external_id"] = str(posting["id"])

        fields.update(_compensation(posting))
        yield fields
