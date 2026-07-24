
import html
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

SITE = "https://jobs.framework.ventures/"
EXTRA_HOSTS = []

_HOST = urlparse(SITE).netloc
_MAX_POSTINGS = 6
_ROLE_RE = re.compile(
    r"(engineer|engineering|developer|protocol|backend|back-end|software|"
    r"infrastructure|security|smart-contract|blockchain|devops|sre|architect)",
    re.IGNORECASE,
)


def _xml_locations(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError, TypeError):
        return []
    return [
        node.text.strip()
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "loc"
        and node.text
        and node.text.strip()
    ]


def _same_host(url):
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == _HOST


def _description_text(value):
    if not value or str(value).strip().lower() in {"null", "none", "undefined"}:
        return None
    text = html.unescape(str(value))
    text = re.sub(
        r"<\s*(?:br|hr)\b[^>]*?/?>|</\s*(?:address|article|blockquote|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|ol|p|pre|section|table|"
        r"td|th|tr|ul)\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text).strip()
    return text or None


def _next_job(page):
    match = re.search(
        r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
        return data["props"]["pageProps"]["initialState"]["jobs"]["currentJob"]
    except (KeyError, TypeError, ValueError):
        return None


def _names(values):
    result = []
    for value in values or []:
        name = value.get("name") if isinstance(value, dict) else value
        if isinstance(name, str) and name.strip() and name.strip() not in result:
            result.append(name.strip())
    return result


def _record(detail_url, job):
    if not isinstance(job, dict):
        return None
    if str(job.get("status", "")).lower() not in {"", "active"}:
        return None
    if job.get("visibility") not in (None, "", "visible"):
        return None

    title = job.get("title")
    organization = job.get("organization") or {}
    company = organization.get("name")
    description = _description_text(job.get("description"))
    locations = _names(job.get("locations"))

    if not title or not company or not description or not locations:
        return None

    record = {
        "url": detail_url,
        "title": title,
        "company": company,
        "location": locations[0],
        "locations": locations,
        "description": description,
        "source": SITE,
        "external_id": str(job["id"]) if job.get("id") is not None else None,
        "posted_at": job.get("postedAt"),
        "expires_at": job.get("expiresAt"),
        "apply_url": job.get("url"),
    }

    employment_types = _names(job.get("employmentTypes"))
    if employment_types:
        record["employment_type"] = ", ".join(employment_types)

    functions = _names(job.get("jobFunctions"))
    if functions:
        record["department"] = ", ".join(functions)

    if job.get("compensationPublic") is True:
        minimum = job.get("compensationAmountMinCents")
        maximum = job.get("compensationAmountMaxCents")
        if isinstance(minimum, (int, float)):
            record["salary_min"] = minimum / 100
        if isinstance(maximum, (int, float)):
            record["salary_max"] = maximum / 100
        if "salary_min" in record or "salary_max" in record:
            if job.get("compensationCurrency"):
                record["salary_currency"] = job["compensationCurrency"]
            if job.get("compensationPeriod"):
                record["salary_period"] = job["compensationPeriod"]

    return {
        key: value
        for key, value in record.items()
        if value is not None and value != "" and value != []
    }


def crawl(ctx):
    # The first shard is newest-first; bound detail fetches to the requested
    # recent engineering/protocol/backend window while sampling companies.
    sitemap_url = SITE + "sitemap_jobs1.xml"
    selected = []
    companies = set()
    for url in _xml_locations(ctx.fetch(sitemap_url)):
        match = re.search(r"/companies/([^/]+)/jobs/", urlparse(url).path)
        company_slug = match.group(1) if match else None
        if (
            len(selected) < _MAX_POSTINGS
            and company_slug
            and company_slug not in companies
            and _same_host(url)
            and _ROLE_RE.search(url)
        ):
            companies.add(company_slug)
            selected.append(url)

    for detail_url in selected:
        job = _next_job(ctx.fetch(detail_url))
        record = _record(detail_url, job)
        if record:
            yield record
