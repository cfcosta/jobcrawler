
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/Neon?includeCompensation=true"
EXTRA_HOSTS = []


def _text_from_html(html):
    if not html:
        return None
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|section)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _salary_from_compensation(compensation):
    if not isinstance(compensation, dict):
        return None

    salary_summaries = []
    for tier in compensation.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if component.get("compensationType") == "Salary":
                summary = _clean(component.get("summary"))
                if summary:
                    salary_summaries.append(summary)
                else:
                    min_value = component.get("minValue")
                    max_value = component.get("maxValue")
                    currency = _clean(component.get("currencyCode"))
                    interval = _clean(component.get("interval"))
                    if min_value is not None and max_value is not None and currency:
                        salary_summaries.append(f"{currency} {min_value} - {max_value}" + (f" per {interval}" if interval else ""))

    if salary_summaries:
        return " / ".join(dict.fromkeys(salary_summaries))

    return _clean(compensation.get("compensationTierSummary"))


def _job_dict(record):
    description = _clean(record.get("descriptionPlain")) or _text_from_html(record.get("descriptionHtml"))

    job = {
        "url": _clean(record.get("jobUrl")),
        "title": _clean(record.get("title")),
        "company": "Neon",
        "location": _clean(record.get("location")),
        "description": description,
        "source": SITE,
    }

    optional_fields = {
        "employment_type": _clean(record.get("employmentType")),
        "workplace_type": _clean(record.get("workplaceType")),
        "department": _clean(record.get("department") or record.get("team")),
        "posted_at": _clean(record.get("publishedAt")),
        "external_id": _clean(record.get("id")),
        "apply_url": _clean(record.get("applyUrl")),
        "salary": _salary_from_compensation(record.get("compensation")),
    }
    for key, value in optional_fields.items():
        if value is not None:
            job[key] = value

    return {key: value for key, value in job.items() if value is not None and value != ""}


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "text"):
        response = response.text
    data = json.loads(response)
    for record in data.get("jobs") or []:
        if record.get("isListed") is False:
            continue
        job = _job_dict(record)
        if job.get("url") and job.get("title") and job.get("description"):
            yield job
