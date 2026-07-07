
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/c6bank/jobs?content=true'
EXTRA_HOSTS = []


def _clean_description(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style|iframe)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|li|ul|ol|tr|table|h1|h2|h3|h4|h5|h6)[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub("[ \t\r\f\v]+", " ", text)
    text = re.sub(" *\n *", "\n", text)
    text = re.sub("\n(3,)", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if isinstance(first, dict):
        return first.get("name") or None
    return None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        title = item.get("title") or None
        url = item.get("absolute_url") or None
        location = (item.get("location") or {}).get("name") or None
        description = _clean_description(item.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "C6 Bank",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]

        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        department = _first_name(item.get("departments"))
        if department:
            job["department"] = department

        yield job
