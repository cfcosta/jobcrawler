
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

SITE = "https://arc.dev/remote-jobs/golang"
EXTRA_HOSTS = []
MAX_JOBS = 8


def _next_data(text):
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        text,
        re.I | re.S,
    )
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _clean_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _iso_from_epoch(value):
    if value in (None, ""):
        return None
    try:
        value = int(value)
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _location(job):
    parts = []
    for key in ("requiredCountries", "requiredLocations"):
        value = job.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value if x)
        elif value:
            parts.append(str(value))
    if not parts:
        for key in ("location", "timezone", "timeZone"):
            value = job.get(key)
            if value:
                parts.append(str(value))
    return ", ".join(dict.fromkeys(parts)) or "Remote"


def _employment_type(job):
    value = job.get("jobType") or job.get("contractType")
    if not value:
        return None
    mapping = {
        "permanent": "Full-time",
        "fulltime": "Full-time",
        "full-time": "Full-time",
        "contract": "Contract",
        "parttime": "Part-time",
        "part-time": "Part-time",
    }
    return mapping.get(str(value).lower(), str(value))


def _salary_raw(job):
    min_value = job.get("minAnnualSalary")
    max_value = job.get("maxAnnualSalary")
    interval = "year"
    if min_value is None and max_value is None:
        min_value = job.get("minHourlyRate")
        max_value = job.get("maxHourlyRate")
        interval = "hour"
    if min_value is None and max_value is None:
        return None
    if min_value is not None and max_value is not None and min_value != max_value:
        return "USD %s - %s per %s" % (min_value, max_value, interval)
    value = min_value if min_value is not None else max_value
    return "USD %s per %s" % (value, interval)


def _add_if(out, key, value):
    if value not in (None, ""):
        out[key] = value


def crawl(ctx):
    listing_html = ctx.fetch(SITE)
    data = _next_data(listing_html)
    page_props = data.get("props", {}).get("pageProps", {})
    grouped_jobs = [
        (page_props.get("arcJobs") or [], "arc"),
        (page_props.get("externalJobs") or [], "external"),
    ]

    seen = set()
    emitted = 0
    for jobs, kind in grouped_jobs:
        for listing_job in jobs:
            if emitted >= MAX_JOBS:
                return
            random_key = listing_job.get("randomKey")
            slug = listing_job.get("urlString")
            if not random_key or not slug:
                continue
            if kind == "arc":
                path = "/remote-jobs/details/%s-%s" % (slug, random_key)
            else:
                path = "/remote-jobs/j/%s-%s" % (slug, random_key)
            url = urljoin(SITE, path)
            if url in seen:
                continue
            seen.add(url)

            try:
                detail_html = ctx.fetch(url)
            except Exception:
                continue

            detail_data = _next_data(detail_html)
            detail_props = detail_data.get("props", {}).get("pageProps", {})
            job = detail_props.get("job") or listing_job
            company_obj = detail_props.get("company") or job.get("company") or listing_job.get("company") or {}

            if job.get("closed") is True or job.get("state") in {"closed", "archived"}:
                continue

            title = job.get("title") or listing_job.get("title")
            company = (
                job.get("companyName")
                or company_obj.get("name")
                or (listing_job.get("company") or {}).get("name")
                or ("Arc Exclusive" if kind == "arc" else None)
            )
            description = _clean_text(job.get("description"))
            if not title or not company or not description:
                continue

            out = {
                "url": url,
                "title": title,
                "company": company,
                "location": _location(job),
                "description": description,
                "source": SITE,
            }
            _add_if(out, "external_id", random_key)
            _add_if(out, "employment_type", _employment_type(job))
            _add_if(out, "department", job.get("jobRole") or job.get("positionType") or job.get("discipline"))
            _add_if(out, "posted_at", _iso_from_epoch(job.get("postedAt") or job.get("createdAt")))
            _add_if(out, "apply_url", job.get("url") or job.get("jobLink") or job.get("interviewLink"))
            _add_if(out, "workplace_type", company_obj.get("remoteLevel"))
            _add_if(out, "salary", _salary_raw(job))

            emitted += 1
            yield out
