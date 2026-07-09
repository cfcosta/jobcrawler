
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/anyscale?includeCompensation=true"
EXTRA_HOSTS = []


def _strip_html(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _first_salary_component(job):
    comp = job.get("compensation") or {}
    for component in comp.get("summaryComponents") or []:
        if component.get("minValue") is not None or component.get("maxValue") is not None:
            return component
    for tier in comp.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("minValue") is not None or component.get("maxValue") is not None:
                return component
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _strip_html(job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Anyscale",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "department": _clean(job.get("department")),
            "posted_at": _clean(job.get("publishedAt")),
        }

        salary = _first_salary_component(job)
        if salary:
            if salary.get("minValue") is not None:
                item["salary_min"] = salary.get("minValue")
            if salary.get("maxValue") is not None:
                item["salary_max"] = salary.get("maxValue")
            currency = _clean(salary.get("currencyCode"))
            interval = _clean(salary.get("interval"))
            if currency:
                item["salary_currency"] = currency
            if interval:
                item["salary_period"] = interval

        yield item
