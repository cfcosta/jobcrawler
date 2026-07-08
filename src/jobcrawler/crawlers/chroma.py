
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/trychroma?includeCompensation=true"
EXTRA_HOSTS = []

def _html_to_text(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _compensation(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    salaries = []
    for component in components:
        if not isinstance(component, dict):
            continue
        item = {}
        value = component.get("minValue")
        if value is not None and value != "":
            item["min"] = value
        value = component.get("maxValue")
        if value is not None and value != "":
            item["max"] = value
        value = component.get("currencyCode")
        if value is not None and value != "":
            item["currency"] = value
        value = component.get("interval")
        if value is not None and value != "":
            item["interval"] = value
        if item:
            salaries.append(item)
    return salaries or None

def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Chroma",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _clean(job.get("id"))
        if external_id is not None:
            item["external_id"] = external_id

        department = _clean(job.get("department")) or _clean(job.get("team"))
        if department is not None:
            item["department"] = department

        employment_type = _clean(job.get("employmentType"))
        if employment_type is not None:
            item["employment_type"] = employment_type

        workplace_type = _clean(job.get("workplaceType"))
        if workplace_type is not None:
            item["workplace_type"] = workplace_type

        posted_at = _clean(job.get("publishedAt"))
        if posted_at is not None:
            item["posted_at"] = posted_at

        apply_url = _clean(job.get("applyUrl"))
        if apply_url is not None:
            item["apply_url"] = apply_url

        salary = _compensation(job)
        if salary is not None:
            item["salary"] = salary

        yield item
