
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:div|p|li|ul|ol|section|article|h[1-6]|table|tr|td|th)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<script\b[^>]*>.*?</script\s*>", " ", value, flags=re.I)
    value = re.sub(r"(?s)<style\b[^>]*>.*?</style\s*>", " ", value, flags=re.I)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _display(value):
    if isinstance(value, list):
        values = [str(item) for item in value if item not in (None, "")]
        return ", ".join(values) if values else None
    return value


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        title = item.get("jobTitle")
        company = item.get("companyName")
        location = item.get("jobGeo")
        url = item.get("url")
        description_html = item.get("jobDescription")
        if not all((title, company, location, url, description_html)):
            continue

        description = _plain_text(description_html)
        if not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": "Jobicy",
        }

        optional = {
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "employment_type": _display(item.get("jobType")),
            "department": _display(item.get("jobIndustry")),
            "posted_at": item.get("pubDate"),
            "salary_min": item.get("salaryMin"),
            "salary_max": item.get("salaryMax"),
            "salary_currency": item.get("salaryCurrency"),
            "salary_period": item.get("salaryPeriod"),
        }
        fields.update({
            key: value
            for key, value in optional.items()
            if value not in (None, "", [])
        })
        yield Job(**fields)
