
import json
import re
import xml.etree.ElementTree as ET
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://jobs.framework.ventures/jobs"
EXTRA_HOSTS = []
SITEMAP = "https://jobs.framework.ventures/sitemap.xml"
MAX_JOBS = 25


def _plain_text(markup):
    text = unescape(markup or "")
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _xml_entries(xml_text):
    root = ET.fromstring(xml_text)
    result = []
    for node in root:
        if node.tag.rsplit("}", 1)[-1] != "url":
            continue
        values = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
            for child in node
        }
        url = values.get("loc")
        if url and "/companies/" in url and "/jobs/" in url:
            result.append((values.get("lastmod", ""), url))
    return result


def _job_sitemaps(xml_text):
    root = ET.fromstring(xml_text)
    urls = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "loc":
            url = (node.text or "").strip()
            if re.search(r"/sitemap_jobs\d*\.xml$", url):
                urls.append(url)
    return urls


def _next_job(page):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    if not match:
        return None
    data = json.loads(unescape(match.group(1)))
    return (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("currentJob")
    )


def crawl(ctx):
    index = ctx.fetch(SITEMAP)
    sitemap_urls = _job_sitemaps(index)[:1]

    entries = []
    for sitemap_url in sitemap_urls:
        entries.extend(_xml_entries(ctx.fetch(sitemap_url)))

    entries.sort(key=lambda item: item[0], reverse=True)
    seen_companies = set()
    selected = []
    for lastmod, url in entries:
        match = re.search(r"/companies/([^/]+)/jobs/", url)
        company_slug = match.group(1) if match else None
        if company_slug and company_slug not in seen_companies:
            seen_companies.add(company_slug)
            selected.append((lastmod, url))
        if len(selected) >= 5:
            break

    for _, page_url in selected:
        job = _next_job(ctx.fetch(page_url))
        if not isinstance(job, dict):
            continue
        if job.get("status") != "active" or job.get("visibility") != "visible":
            continue

        title = job.get("title")
        organization = job.get("organization") or {}
        company = organization.get("name")
        description = _plain_text(job.get("description"))
        locations = [
            item.get("name")
            for item in (job.get("locations") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if not (title and company and description and locations):
            continue

        kwargs = {
            "url": page_url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": str(job["id"]) if job.get("id") is not None else None,
            "apply_url": job.get("url"),
            "posted_at": job.get("postedAt"),
            "expires_at": job.get("expiresAt"),
        }

        employment_types = [
            value for value in (job.get("employmentTypes") or []) if value
        ]
        if employment_types:
            kwargs["employment_type"] = ", ".join(employment_types)

        functions = [
            item.get("name")
            for item in (job.get("jobFunctions") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if functions:
            kwargs["department"] = ", ".join(functions)

        minimum = job.get("compensationAmountMinCents")
        maximum = job.get("compensationAmountMaxCents")
        currency = job.get("compensationCurrency")
        period = job.get("compensationPeriod")
        if job.get("compensationPublic") and currency and (
            minimum is not None or maximum is not None
        ):
            if minimum is not None:
                kwargs["salary_min"] = minimum / 100
            if maximum is not None:
                kwargs["salary_max"] = maximum / 100
            kwargs["salary_currency"] = currency
            if period and period != "period_not_defined":
                kwargs["salary_period"] = period

        yield Job(**{key: value for key, value in kwargs.items() if value is not None})
