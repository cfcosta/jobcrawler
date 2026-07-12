
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/samsara/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        data = json.loads(response)
    elif isinstance(response, bytes):
        data = json.loads(response.decode("utf-8"))
    elif hasattr(response, "json"):
        data = response.json()
    elif hasattr(response, "text"):
        data = json.loads(response.text)
    else:
        data = response

    for item in data.get("jobs", []):
        metadata = {
            entry.get("name"): entry.get("value")
            for entry in item.get("metadata", [])
            if entry.get("name") and entry.get("value") not in (None, "")
        }
        location = (item.get("location") or {}).get("name")
        job = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": "Samsara",
            "location": location,
            "description": _text(item.get("content")),
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": item.get("absolute_url"),
            "employment_type": metadata.get("Employment Type"),
            "department": metadata.get("Job Family Group"),
            "posted_at": item.get("first_published"),
        }
        yield {key: value for key, value in job.items() if value is not None}
