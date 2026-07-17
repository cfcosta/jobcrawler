
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/spacex/jobs?content=true"
EXTRA_HOSTS = []

_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
    r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|"
    r"table|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_ANY_TAG = re.compile(r"<[^>]+>")


def _description(value):
    value = html.unescape(value)
    value = _BLOCK_TAGS.sub("\n", value)
    value = _ANY_TAG.sub("", value)
    value = html.unescape(value)
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name:
            value = item.get("value")
            if value not in (None, "", []):
                return value
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data["jobs"]:
        kwargs = {
            "source": SITE,
            "url": item["absolute_url"],
            "title": item["title"].strip(),
            "company": "SpaceX",
            "location": item["location"]["name"],
            "description": _description(item["content"]),
            "external_id": str(item["id"]),
            "posted_at": item["first_published"],
        }

        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if offices:
            kwargs["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            kwargs["department"] = departments[0]

        employment_type = _metadata_value(item, "Employment Type")
        if employment_type:
            kwargs["employment_type"] = employment_type

        deadline = item.get("application_deadline")
        if deadline:
            kwargs["expires_at"] = deadline

        ranges = item.get("pay_input_ranges") or []
        currencies = {entry.get("currency_type") for entry in ranges}
        if ranges and len(currencies) == 1 and None not in currencies:
            minimums = [
                entry["min_cents"] / 100
                for entry in ranges
                if entry.get("min_cents") is not None
            ]
            maximums = [
                entry["max_cents"] / 100
                for entry in ranges
                if entry.get("max_cents") is not None
            ]
            if minimums:
                kwargs["salary_min"] = min(minimums)
            if maximums:
                kwargs["salary_max"] = max(maximums)
            kwargs["salary_currency"] = next(iter(currencies))

        yield Job(**kwargs)
