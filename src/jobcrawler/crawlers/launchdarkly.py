
import html
import json
import re
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/launchdarkly/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|table|section|article|blockquote)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not isinstance(items, list):
        return None
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(item.get("name"))
    return ", ".join(names) if names else None


def _job_obj(fields):
    fields = {k: v for k, v in fields.items() if v not in (None, "")}
    try:
        return Job(**fields)
    except NameError:
        return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        url = item.get("absolute_url")
        location_data = item.get("location") or {}
        location = location_data.get("name") if isinstance(location_data, dict) else None
        description = _html_to_text(item.get("content"))

        if not (title and url and location and description):
            continue

        external_id = item.get("id")
        if external_id is None:
            external_id = item.get("internal_job_id")

        yield _job_obj({
            "url": url,
            "title": title,
            "company": "LaunchDarkly",
            "location": location,
            "description": description,
            "source": SITE,
            "department": _first_name(item.get("departments")),
            "external_id": str(external_id) if external_id is not None else None,
        })
