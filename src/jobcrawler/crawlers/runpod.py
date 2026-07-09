
import json
import re
import html

SITE = "https://api.ashbyhq.com/posting-api/job-board/runpod?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return text.strip() or None


def _description(job):
    text = job.get("descriptionPlain")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return _clean_html(job.get("descriptionHtml"))


def _salary_fields(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        fields = {}
        if min_value is not None:
            fields["salary_min"] = min_value
        if max_value is not None:
            fields["salary_max"] = max_value
        if currency:
            fields["salary_currency"] = currency
        if interval:
            fields["salary_period"] = interval
        return fields
    return {}


def _first_nonempty(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if not isinstance(job, dict):
            continue

        title = _first_nonempty(job.get("title"))
        description = _description(job)
        url = _first_nonempty(job.get("jobUrl"))
        location = _first_nonempty(job.get("location"))

        if not (title and description and url and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Runpod",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "external_id": job.get("id"),
            "department": _first_nonempty(job.get("department"), job.get("team")),
            "employment_type": _first_nonempty(job.get("employmentType")),
            "workplace_type": _first_nonempty(job.get("workplaceType")),
            "apply_url": _first_nonempty(job.get("applyUrl")),
        }
        if optional["workplace_type"] is None and job.get("isRemote") is True:
            optional["workplace_type"] = "Remote"

        for key, value in optional.items():
            if value is not None:
                item[key] = value

        item.update(_salary_fields(job))
        yield item
