
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/render?includeCompensation=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")


def _text_from_html(value):
    if not value:
        return None
    text = _TAG_RE.sub(" ", unescape(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _salary_fields(job):
    comp = job.get("compensation") or {}
    components = comp.get("summaryComponents") or []
    for component in components:
        if component.get("compensationType") != "Salary":
            continue
        fields = {}
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = _clean(component.get("currencyCode"))
        interval = _clean(component.get("interval"))
        if min_value is not None:
            fields["salary_min"] = min_value
        if max_value is not None:
            fields["salary_max"] = max_value
        if currency:
            fields["salary_currency"] = currency
        if interval:
            fields["salary_interval"] = interval
        return fields
    return {}


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for job in payload.get("jobs", []):
        description = _clean(job.get("descriptionPlain")) or _text_from_html(job.get("descriptionHtml"))
        item = {
            "url": _clean(job.get("jobUrl")),
            "title": _clean(job.get("title")),
            "company": "Render",
            "location": _clean(job.get("location")),
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "department": _clean(job.get("department") or job.get("team")),
        }
        item.update(_salary_fields(job))
        yield {key: value for key, value in item.items() if value is not None}
