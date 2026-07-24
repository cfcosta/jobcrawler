
import json
import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import urlsplit

from jobcrawler.runtime.contract import Job


SITE = "https://jobs.framework.ventures/jobs"
EXTRA_HOSTS = []

SITEMAP_URL = "https://jobs.framework.ventures/sitemap.xml"
WINDOW_SIZE = 6


def _plain_text(value):
    if not value:
        return None
    value = re.sub(
        r"<(?:br|hr)\s*/?>|</(?:address|article|aside|blockquote|div|h[1-6]|li|"
        r"main|ol|p|pre|section|table|td|th|tr|ul)>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _job_urls(sitemap_text):
    root = ET.fromstring(sitemap_text)
    entries = []
    for node in root:
        loc = None
        lastmod = ""
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "loc":
                loc = (child.text or "").strip()
            elif name == "lastmod":
                lastmod = (child.text or "").strip()
        if not loc:
            continue
        parsed = urlsplit(loc)
        if (
            parsed.hostname == "jobs.framework.ventures"
            and re.search(r"^/companies/[^/]+/jobs/[^/]+$", parsed.path)
        ):
            entries.append((lastmod, loc))

    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    seen = set()
    for _, url in entries:
        clean = url.split("#", 1)[0]
        if clean not in seen:
            seen.add(clean)
            yield clean


def _next_job(page):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
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


def _named_values(values):
    result = []
    for value in values or []:
        if isinstance(value, dict):
            name = value.get("name") or value.get("description")
        elif isinstance(value, str):
            name = value
        else:
            name = None
        if name and name not in result:
            result.append(name)
    return result


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    urls = list(_job_urls(sitemap))[:WINDOW_SIZE]

    for page_url in urls:
        page = ctx.fetch(page_url)
        try:
            record = _next_job(page)
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("status") != "active" or record.get("visibility") != "visible":
            continue

        title = record.get("title")
        organization = record.get("organization") or {}
        company = organization.get("name")
        description = _plain_text(record.get("description"))
        locations = _named_values(record.get("locations"))
        if not title or not company or not description or not locations:
            continue

        fields = {
            "url": page_url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": str(record["id"]) if record.get("id") is not None else None,
            "apply_url": record.get("url"),
            "posted_at": record.get("postedAt"),
            "expires_at": record.get("expiresAt"),
        }

        employment_types = _named_values(record.get("employmentTypes"))
        if employment_types:
            fields["employment_type"] = ", ".join(employment_types)

        location_words = " ".join(locations).lower()
        if "remote" in location_words or "anywhere" in location_words:
            fields["workplace_type"] = "remote"

        minimum = record.get("compensationAmountMinCents")
        maximum = record.get("compensationAmountMaxCents")
        currency = record.get("compensationCurrency")
        period = record.get("compensationPeriod")
        if minimum is not None and currency:
            fields["salary_min"] = minimum / 100
        if maximum is not None and currency:
            fields["salary_max"] = maximum / 100
        if (minimum is not None or maximum is not None) and currency:
            fields["salary_currency"] = currency
        if (
            (minimum is not None or maximum is not None)
            and period
            and period != "period_not_defined"
        ):
            fields["salary_period"] = period

        yield Job(**{key: value for key, value in fields.items() if value is not None})
