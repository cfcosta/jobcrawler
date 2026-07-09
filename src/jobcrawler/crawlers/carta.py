
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/carta/jobs?content=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</p\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_html(job.get("content"))

        item = {
            "url": url,
            "title": title,
            "company": "Carta",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        posted_at = job.get("first_published")
        if posted_at:
            item["posted_at"] = posted_at

        apply_url = job.get("absolute_url")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
