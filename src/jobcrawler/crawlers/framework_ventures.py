
import html
import json
import re
from datetime import datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

from jobcrawler.runtime.contract import Job


SITE = "https://jobs.framework.ventures/jobs"
EXTRA_HOSTS = []

# Getro portfolio sitemaps retain years of records. Fetching the freshest window
# covers current additions while avoiding thousands of historical detail requests.
MAX_DETAIL_PAGES = 8


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _description(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(?:p|div|li|h[1-6]|tr)\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _xml_entries(body):
    root = ElementTree.fromstring(body)
    entries = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "url":
            continue
        url = None
        lastmod = None
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "loc":
                url = _text(child.text)
            elif name == "lastmod":
                lastmod = _text(child.text)
        if url:
            entries.append((lastmod or "", url))
    return entries


def _current_job(body):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        body,
        re.I | re.S,
    )
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError):
        return None
    return (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("currentJob")
    )


def crawl(ctx):
    root = ctx.fetch("https://jobs.framework.ventures/sitemap.xml")
    sitemap_urls = re.findall(
        r"<loc>\s*(https://jobs\.framework\.ventures/sitemap_jobs\d+\.xml)\s*</loc>",
        root,
        re.I,
    )

    entries = []
    for sitemap_url in sitemap_urls:
        entries.extend(_xml_entries(ctx.fetch(sitemap_url)))

    # De-duplicate before applying the freshness window.
    newest_by_url = {}
    for lastmod, url in entries:
        if urlsplit(url).hostname != "jobs.framework.ventures":
            continue
        if "/companies/" not in url or "/jobs/" not in url:
            continue
        if lastmod >= newest_by_url.get(url, ""):
            newest_by_url[url] = lastmod

    ordered = sorted(
        ((lastmod, url) for url, lastmod in newest_by_url.items()),
        reverse=True,
    )

    for _, url in ordered[:MAX_DETAIL_PAGES]:
        job = _current_job(ctx.fetch(url))
        if not isinstance(job, dict):
            continue
        if job.get("status") != "active" or job.get("visibility") != "visible":
            continue

        title = _text(job.get("title"))
        description = _description(job.get("description"))
        organization = job.get("organization") or {}
        company = _text(organization.get("name"))
        location_rows = job.get("locations") or []
        locations = [
            name
            for name in (_text(row.get("name")) for row in location_rows if isinstance(row, dict))
            if name
        ]
        if not title or not company or not description or len(description) < 40 or not locations:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": company,
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
            "external_id": _text(job.get("id")),
            "posted_at": _text(job.get("postedAt")),
            "expires_at": _text(job.get("expiresAt")),
        }

        employment_types = [
            value for value in (_text(item) for item in (job.get("employmentTypes") or []))
            if value
        ]
        if employment_types:
            fields["employment_type"] = ", ".join(employment_types)

        external_url = _text(job.get("url"))
        if external_url and external_url != url:
            fields["apply_url"] = external_url

        minimum = job.get("compensationAmountMinCents")
        maximum = job.get("compensationAmountMaxCents")
        currency = _text(job.get("compensationCurrency"))
        period = _text(job.get("compensationPeriod"))
        if job.get("compensationPublic") is True and currency and (
            minimum is not None or maximum is not None
        ):
            fields["salary_min"] = minimum / 100 if minimum is not None else None
            fields["salary_max"] = maximum / 100 if maximum is not None else None
            fields["salary_currency"] = currency
            if period and period != "period_not_defined":
                fields["salary_period"] = period

        yield Job(**{key: value for key, value in fields.items() if value is not None})
