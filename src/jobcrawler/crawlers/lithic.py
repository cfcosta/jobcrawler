
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/lithic/jobs?content=true"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        title = (item.get("title") or "").strip()
        url = (item.get("absolute_url") or "").strip()
        location = ((item.get("location") or {}).get("name") or "").strip()
        description = _text_from_html(item.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Lithic",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        apply_url = (item.get("absolute_url") or "").strip()
        if apply_url:
            job["apply_url"] = apply_url

        yield job
