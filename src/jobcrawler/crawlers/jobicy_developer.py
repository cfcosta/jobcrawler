import html
import json
import re
from urllib.parse import urlparse

from jobcrawler.runtime.contract import Job

SITE = "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev"
EXTRA_HOSTS = []


def _plain_text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", text)
    text = re.sub(
        r"(?i)<\s*(br|p|div|li|h[1-6]|section|article|tr|table|ul|ol)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr|table|ul|ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _first(record, *names):
    for name in names:
        value = record.get(name)
        if value is not None and value != "" and value != []:
            return value
    return None


def _list_text(value):
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(values) or None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)
    records = payload.get("jobs") or payload.get("data") or []

    for record in records:
        if not isinstance(record, dict):
            continue

        url = _first(record, "url", "jobUrl", "jobURL")
        title = _first(record, "jobTitle", "title")
        company = _first(record, "companyName", "company")
        location = _first(record, "jobGeo", "location")
        description = _plain_text(
            _first(record, "jobDescription", "description", "descriptionHtml")
        )

        required = (url, title, company, location)
        if not all(isinstance(value, str) and value.strip() for value in required):
            continue
        if urlparse(url).hostname not in {"jobicy.com", "www.jobicy.com"}:
            continue
        if not description or len(description) < 200:
            continue

        fields = {
            "url": url.strip(),
            "title": title.strip(),
            "company": company.strip(),
            "location": location.strip(),
            "description": description,
            "source": SITE,
            "workplace_type": "remote",
        }

        external_id = _first(record, "id", "jobId", "external_id")
        if external_id is not None:
            fields["external_id"] = str(external_id)

        employment_type = _list_text(
            _first(record, "jobType", "employmentType", "employment_type")
        )
        if employment_type:
            fields["employment_type"] = employment_type

        department = _list_text(
            _first(record, "jobIndustry", "department", "category")
        )
        if department:
            fields["department"] = department

        posted_at = _first(record, "pubDate", "publishedAt", "posted_at")
        if posted_at:
            fields["posted_at"] = posted_at

        salary_min = _first(record, "salaryMin", "annualSalaryMin")
        salary_max = _first(record, "salaryMax", "annualSalaryMax")
        salary_currency = _first(record, "salaryCurrency", "currency")
        salary_period = _first(record, "salaryPeriod", "payPeriod")

        if salary_min is not None:
            fields["salary_min"] = salary_min
        if salary_max is not None:
            fields["salary_max"] = salary_max
        if salary_currency:
            fields["salary_currency"] = salary_currency
        if salary_period:
            fields["salary_period"] = salary_period

        yield Job(**fields)
