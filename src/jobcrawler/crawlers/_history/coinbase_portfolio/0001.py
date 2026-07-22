import json
import re
from html import unescape
from urllib.parse import urldefrag
from xml.etree import ElementTree as ET
from jobcrawler.runtime.contract import Job


SITE = "https://coinbase.getro.com/"
EXTRA_HOSTS = []

# Detail pages are large, so follow the operator-directed rolling window.
WINDOW_SIZE = 8


def _plain_text(value):
    if not value:
        return None
    value = unescape(value)
    value = re.sub(
        r"<(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<li\b[^>]*>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _next_data(page):
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    try:
        payload = json.loads(unescape(match.group(1)))
    except (TypeError, ValueError):
        return None
    return (
        payload.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("jobs", {})
        .get("currentJob")
    )


def _sitemap_jobs(xml_text):
    root = ET.fromstring(xml_text)
    rows = []
    for node in root:
        fields = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
            for child in node
        }
        url = fields.get("loc")
        if not url or "/jobs/" not in url:
            continue
        url = urldefrag(url)[0]
        if url.startswith(SITE + "companies/"):
            rows.append((fields.get("lastmod") or "", url))

    rows.sort(key=lambda row: row[0], reverse=True)
    seen = set()
    for _, url in rows:
        if url not in seen:
            seen.add(url)
            yield url


def crawl(ctx):
    sitemap_response = ctx.fetch(SITE + "sitemap.xml")
    urls = list(_sitemap_jobs(sitemap_response))[:WINDOW_SIZE]

    for url in urls:
        response = ctx.fetch(url)
        record = _next_data(response)
        if not isinstance(record, dict):
            continue
        if record.get("status") != "active":
            continue
        if record.get("visibility") not in (None, "visible"):
            continue

        title = record.get("title")
        description = _plain_text(record.get("description"))
        organization = record.get("organization") or {}
        company = organization.get("name")
        if not title or not description or not company:
            continue

        location_names = [
            item.get("name")
            for item in (record.get("locations") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        location = location_names[0] if location_names else None

        functions = [
            item.get("name")
            for item in (record.get("jobFunctions") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        employment_types = [
            value for value in (record.get("employmentTypes") or []) if value
        ]

        minimum = record.get("compensationAmountMinCents")
        maximum = record.get("compensationAmountMaxCents")
        currency = record.get("compensationCurrency")
        period = record.get("compensationPeriod")
        compensation_public = record.get("compensationPublic") is True
        if period == "period_not_defined":
            period = None

        apply_url = record.get("url") or record.get("applicationPath")
        external_id = record.get("id")

        yield Job(
            url=url,
            source=SITE,
            title=title,
            company=company,
            location=location,
            locations=location_names or None,
            description=description,
            department=", ".join(functions) if functions else None,
            employment_type=", ".join(employment_types) if employment_types else None,
            salary_min=(minimum / 100.0) if compensation_public and minimum is not None else None,
            salary_max=(maximum / 100.0) if compensation_public and maximum is not None else None,
            salary_currency=currency if compensation_public else None,
            salary_period=period if compensation_public else None,
            posted_at=record.get("postedAt"),
            expires_at=record.get("expiresAt"),
            external_id=str(external_id) if external_id is not None else None,
            apply_url=apply_url,
        )
