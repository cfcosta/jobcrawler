
import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/sentry?includeCompensation=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _interval(value):
    if not value:
        return None
    value = str(value).strip().lower()
    if value in ("1 year", "year", "yearly", "annual"):
        return "year"
    if value in ("1 hour", "hour", "hourly"):
        return "hour"
    if value in ("1 month", "month", "monthly"):
        return "month"
    return value


def _salary(job):
    comp = job.get("compensation") or {}
    for item in comp.get("summaryComponents") or []:
        if item.get("compensationType") != "Salary":
            continue
        out = {}
        if item.get("minValue") is not None:
            out["salary_min"] = item.get("minValue")
        if item.get("maxValue") is not None:
            out["salary_max"] = item.get("maxValue")
        currency = _clean(item.get("currencyCode"))
        interval = _interval(item.get("interval"))
        if currency:
            out["salary_currency"] = currency
        if interval:
            out["salary_interval"] = interval
        if out:
            return out

    summary = _clean(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"))
    if summary:
        return {"salary": summary}
    return {}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        item = {
            "url": _clean(job.get("jobUrl")) or SITE,
            "title": _clean(job.get("title")),
            "company": "Sentry",
            "location": _clean(job.get("location")),
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "department": _clean(job.get("department") or job.get("team")),
            "posted_at": _clean(job.get("publishedAt")),
        }
        item.update(_salary(job))
        yield {key: value for key, value in item.items() if value is not None}
