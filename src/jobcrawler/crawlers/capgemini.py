
import json
import re
import html

SITE = "https://cg-jobstream-api.azurewebsites.net/api/job-search?country_code=pt-br&size=100"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _set_if_value(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    raw = ctx.fetch(SITE)
    payload = json.loads(raw)
    for item in payload.get("data", []):
        title = item.get("title")
        location = item.get("location")
        description = _text_from_html(item.get("description")) or item.get("description_stripped")
        url = item.get("apply_job_url") or item.get("wp_url")
        if not (title and location and description and url):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Capgemini",
            "location": location,
            "description": description,
            "source": SITE,
        }
        _set_if_value(job, "apply_url", item.get("apply_job_url"))
        _set_if_value(job, "external_id", item.get("id") or item.get("_id") or item.get("ref"))
        _set_if_value(job, "employment_type", item.get("contract_type"))
        _set_if_value(job, "department", item.get("department"))
        yield job
