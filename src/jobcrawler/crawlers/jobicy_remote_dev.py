
import html
import json
import re

SITE = "https://jobicy.com/api/v2/remote-jobs?industry=dev"
EXTRA_HOSTS = []


def _strip_html(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h[1-6]|li|ul|ol|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first(values):
    if isinstance(values, list):
        for value in values:
            if value:
                return str(value)
    if values:
        return str(values)
    return None


def _salary(job):
    min_value = job.get("salaryMin")
    max_value = job.get("salaryMax")
    currency = job.get("salaryCurrency")
    period = job.get("salaryPeriod")
    parts = []
    if min_value not in (None, "") and max_value not in (None, ""):
        parts.append(f"{min_value}-{max_value}")
    elif min_value not in (None, ""):
        parts.append(str(min_value))
    elif max_value not in (None, ""):
        parts.append(str(max_value))
    if currency:
        parts.append(str(currency))
    if period:
        parts.append(str(period))
    return " ".join(parts) or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)
    for item in data.get("jobs") or []:
        url = item.get("url")
        title = item.get("jobTitle")
        company = item.get("companyName")
        description = _strip_html(item.get("jobDescription") or item.get("jobExcerpt"))
        location = item.get("jobGeo") or "Remote"
        if not (url and title and company and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": str(location),
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "employment_type": _first(item.get("jobType")),
            "department": _first(item.get("jobIndustry")),
            "posted_at": item.get("pubDate") or None,
            "salary": _salary(item),
        }
        yield {key: value for key, value in job.items() if value not in (None, "")}
