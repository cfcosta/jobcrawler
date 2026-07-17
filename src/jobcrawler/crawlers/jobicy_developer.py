import html
import json
import re


SITE = 'https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev'
EXTRA_HOSTS = []


def _text(value):
    if value is None:
        return None
    value = html.unescape(str(value))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _first_text(value):
    if isinstance(value, list):
        value = value[0] if value else None
    return _text(value)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        title = _text(item.get("jobTitle") or item.get("title"))
        company = _text(item.get("companyName") or item.get("company"))
        location = _text(item.get("jobGeo") or item.get("location"))
        description = _text(
            item.get("jobDescription")
            or item.get("description")
            or item.get("content")
        )
        url = item.get("url") or item.get("jobUrl")

        if not all((title, company, location, url, description)):
            continue
        if len(description) < 200:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("pubDate") or item.get("publishedAt"),
            "employment_type": _first_text(item.get("jobType")),
            "department": _first_text(item.get("jobIndustry")),
            "salary_min": item.get("salaryMin"),
            "salary_max": item.get("salaryMax"),
            "salary_currency": item.get("salaryCurrency"),
            "salary_period": item.get("salaryPeriod"),
        }
        yield {key: value for key, value in fields.items() if value is not None}
