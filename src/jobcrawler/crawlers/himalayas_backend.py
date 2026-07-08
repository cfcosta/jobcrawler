
import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://himalayas.app/jobs/api/search?q=backend&sort=recent"
EXTRA_HOSTS = []

# Himalayas search is a remote-search window, not a closed company board.
MAX_PAGES = 5


def _url_with_page(url, page):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _iso_from_epoch(value):
    if value in (None, ""):
        return None
    try:
        import datetime
        return datetime.datetime.fromtimestamp(int(value), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _location(job):
    parts = []
    for value in job.get("locationRestrictions") or []:
        value = str(value).strip()
        if value:
            parts.append(value)
    timezones = job.get("timezoneRestrictions") or []
    if timezones:
        parts.append("UTC " + ", ".join(str(tz) for tz in timezones))
    return "; ".join(parts) or "Remote"


def _salary(job):
    minimum = job.get("minSalary")
    maximum = job.get("maxSalary")
    currency = job.get("currency")
    period = job.get("salaryPeriod")
    if minimum is None and maximum is None:
        return None
    bits = []
    if currency:
        bits.append(str(currency))
    if minimum is not None and maximum is not None:
        bits.append("%s-%s" % (minimum, maximum))
    elif minimum is not None:
        bits.append("from %s" % minimum)
    elif maximum is not None:
        bits.append("up to %s" % maximum)
    if period:
        bits.append(str(period))
    return " ".join(bits) or None


def _make_job(raw):
    url = raw.get("guid") or raw.get("applicationLink")
    title = raw.get("title")
    company = raw.get("companyName")
    description = _clean_html(raw.get("description"))
    if not (url and title and company and description):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": _location(raw),
        "description": description,
        "source": SITE,
    }

    optional = {
        "employment_type": raw.get("employmentType"),
        "department": ", ".join(raw.get("parentCategories") or raw.get("categories") or []) or None,
        "posted_at": _iso_from_epoch(raw.get("pubDate")),
        "expires_at": _iso_from_epoch(raw.get("expiryDate")),
        "external_id": raw.get("guid"),
        "apply_url": raw.get("applicationLink"),
        "salary": _salary(raw),
    }
    if raw.get("locationRestrictions"):
        optional["workplace_type"] = "Remote"

    for key, value in optional.items():
        if value not in (None, ""):
            job[key] = value
    return job


def crawl(ctx):
    seen = set()
    for page in range(1, MAX_PAGES + 1):
        data = json.loads(ctx.fetch(_url_with_page(SITE, page)))
        jobs = data.get("jobs") or []
        if not jobs:
            break

        for raw in jobs:
            job = _make_job(raw)
            if not job or job["url"] in seen:
                continue
            seen.add(job["url"])
            yield job
