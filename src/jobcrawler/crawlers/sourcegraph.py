
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/sourcegraph91/jobs?content=true"


def _clean_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _present(value):
    return value is not None and value != ""


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_text(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Sourcegraph",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if _present(job.get("requisition_id")):
            item["external_id"] = str(job.get("requisition_id"))
        elif job.get("id") is not None:
            item["external_id"] = str(job.get("id"))

        yield item
