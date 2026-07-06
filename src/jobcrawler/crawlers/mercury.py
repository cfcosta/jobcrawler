
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/mercury/jobs?content=true'


def _response_text(response):
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return response.decode("utf-8", "replace")
    if isinstance(response, dict):
        text = response.get("text") or response.get("body") or response.get("content")
        if text is not None:
            return text
    return str(response)


def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not items:
        return None
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(item.get("name"))
    if names:
        return ", ".join(names)
    return None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(_response_text(response))
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        location = (job.get("location") or {}).get("name")
        description = _clean_description(job.get("content"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Mercury",
            "location": location,
            "description": description,
            "source": SITE,
        }

        department = _first_name(job.get("departments"))
        if department:
            item["department"] = department

        external_id = job.get("requisition_id") or job.get("id") or job.get("internal_job_id")
        if external_id is not None:
            item["external_id"] = str(external_id)

        apply_url = job.get("absolute_url")
        if apply_url:
            item["apply_url"] = apply_url

        yield item
