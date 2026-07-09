
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/lancedb?includeCompensation=true"
EXTRA_HOSTS = []


def _strip_html(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
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


def _salary(job):
    comp = job.get("compensation") or {}
    for component in comp.get("summaryComponents") or []:
        if component.get("compensationType") == "Salary":
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            currency = _clean(component.get("currencyCode"))
            interval = _clean(component.get("interval"))
            salary = {}
            if min_value is not None:
                salary["salary_min"] = min_value
            if max_value is not None:
                salary["salary_max"] = max_value
            if currency is not None:
                salary["salary_currency"] = currency
            if interval is not None:
                salary["salary_period"] = interval
            return salary
    return {}


def _job_type():
    try:
        return Job
    except NameError:
        return dict


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    JobType = _job_type()

    for item in data.get("jobs") or []:
        if item.get("isListed") is False:
            continue

        description = _clean(item.get("descriptionPlain"))
        if not description:
            description = _strip_html(item.get("descriptionHtml"))

        values = {
            "url": _clean(item.get("jobUrl")) or SITE,
            "title": _clean(item.get("title")),
            "company": "LanceDB",
            "location": _clean(item.get("location")),
            "description": description,
            "source": SITE,
            "external_id": _clean(item.get("id")),
            "department": _clean(item.get("department")) or _clean(item.get("team")),
            "employment_type": _clean(item.get("employmentType")),
            "workplace_type": _clean(item.get("workplaceType")),
            "posted_at": _clean(item.get("publishedAt")),
        }
        values.update(_salary(item))

        yield JobType(**{key: value for key, value in values.items() if value is not None})
