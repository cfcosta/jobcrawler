
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/tipaltisolutions/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    markup = html.unescape(value)
    markup = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)[^>]*>",
        "\n",
        markup,
    )
    text = re.sub(r"<[^>]+>", "", markup)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return str(item["value"])
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in data.get("jobs") or []:
        location = (item.get("location") or {}).get("name")
        office_names = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        department_names = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Tipalti",
            "location": location,
            "description": _plain_text(item.get("content") or ""),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "employment_type": _metadata_value(item, "Employment Type"),
            "department": ", ".join(department_names) if department_names else None,
            "locations": office_names or None,
        }
        yield Job(**{key: value for key, value in fields.items() if value is not None})
