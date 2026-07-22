import html
import json
import re
from xml.etree import ElementTree as ET

from jobcrawler.runtime.contract import Job

SITE = "https://coinbase.getro.com/"
EXTRA_HOSTS = []
SITEMAP_URL = "https://coinbase.getro.com/sitemap.xml"
WINDOW_SIZE = 5


def _text(value):
    value = value or ""
    value = re.sub(
        r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _body(response):
    return response.text if hasattr(response, "text") else str(response)


def _current_job(page):
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    if not match:
        return None
    data = json.loads(match.group(1))
    return (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("currentJob")
    )


def crawl(ctx):
    sitemap = _body(ctx.fetch(SITEMAP_URL))
    root = ET.fromstring(sitemap)
    entries = []
    for node in root:
        values = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
            for child in node
        }
        url = values.get("loc")
        if url and re.search(r"/companies/[^/]+/jobs/[^/]+$", url):
            entries.append((values.get("lastmod", ""), url))

    entries.sort(key=lambda item: item[0], reverse=True)
    for _, page_url in entries[:WINDOW_SIZE]:
        try:
            job = _current_job(_body(ctx.fetch(page_url)))
        except (ValueError, TypeError):
            continue
        if not isinstance(job, dict):
            continue
        if job.get("status") != "active":
            continue
        if job.get("visibility") not in (None, "visible"):
            continue
        if job.get("passesFilter") is False:
            continue

        title = job.get("title")
        description = _text(job.get("description"))
        organization = job.get("organization") or {}
        company = organization.get("name")
        locations = [
            item.get("name") for item in (job.get("locations") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if not title or not description or not company or not locations:
            continue

        fields = {
            "url": page_url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        if job.get("id") is not None:
            fields["external_id"] = str(job["id"])
        if job.get("postedAt"):
            fields["posted_at"] = job["postedAt"]
        if job.get("expiresAt"):
            fields["expires_at"] = job["expiresAt"]

        employment = [
            str(value) for value in (job.get("employmentTypes") or []) if value
        ]
        if employment:
            fields["employment_type"] = ", ".join(employment)

        apply_url = job.get("applicationPath") or job.get("url")
        if apply_url:
            fields["apply_url"] = apply_url

        departments = [
            item.get("name") for item in (job.get("jobFunctions") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        minimum = job.get("compensationAmountMinCents")
        maximum = job.get("compensationAmountMaxCents")
        currency = job.get("compensationCurrency")
        period = job.get("compensationPeriod")
        if job.get("compensationPublic") is not False and currency and (
            minimum is not None or maximum is not None
        ):
            if minimum is not None:
                fields["salary_min"] = minimum / 100
            if maximum is not None:
                fields["salary_max"] = maximum / 100
            fields["salary_currency"] = currency
            if period and period != "period_not_defined":
                fields["salary_period"] = period

        yield Job(**fields)
