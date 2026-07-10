
import html
import json
import re

SITE = "https://ats.rippling.com/blue-robotics/jobs"
EXTRA_HOSTS = []

def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?is)<(script|style|meta)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|section|article|ul|ol|h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None

def _next_data(page):
    match = re.search(r'<script\b(?=[^>]*\bid=["\']__NEXT_DATA__["\'])[^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))

def _listing_items(data):
    page_props = data.get("props", {}).get("pageProps", {})
    queries = page_props.get("dehydratedState", {}).get("queries", [])
    for query in queries:
        payload = query.get("state", {}).get("data")
        if isinstance(payload, dict) and isinstance(payload.get("items"), list) and "totalItems" in payload:
            return payload.get("items") or []
    return []

def _primary_location(job_post, listing_item):
    locations = job_post.get("workLocations") or []
    if locations:
        return locations[0]
    for loc in listing_item.get("locations") or []:
        if loc.get("name"):
            return loc.get("name")
    return None

def _all_locations(job_post, listing_item):
    result = []
    for loc in job_post.get("workLocations") or []:
        if loc and loc not in result:
            result.append(loc)
    for loc in listing_item.get("locations") or []:
        name = loc.get("name")
        if name and name not in result:
            result.append(name)
    return result

def _workplace_type(job_post, listing_item):
    for loc in listing_item.get("locations") or []:
        value = loc.get("workplaceType")
        if value:
            return value
    for pay in job_post.get("payRangeDetails") or []:
        if pay.get("isRemote") is True:
            return "REMOTE"
    return None

def _apply_pay(job, job_post):
    ranges = job_post.get("payRangeDetails") or []
    if not ranges:
        return
    currencies = {r.get("currency") for r in ranges if r.get("currency")}
    if len(currencies) != 1:
        return
    starts = [r.get("rangeStart") for r in ranges if isinstance(r.get("rangeStart"), (int, float))]
    ends = [r.get("rangeEnd") for r in ranges if isinstance(r.get("rangeEnd"), (int, float))]
    periods = [r.get("frequency") for r in ranges if r.get("frequency")]
    if starts:
        job["salary_min"] = min(starts)
    if ends:
        job["salary_max"] = max(ends)
    if currencies:
        job["salary_currency"] = next(iter(currencies))
    if periods:
        job["salary_period"] = periods[0]
    if starts or ends:
        printed = []
        currency = job.get("salary_currency")
        if starts and ends:
            printed.append("%s %s-%s" % (currency, min(starts), max(ends)))
        elif starts:
            printed.append("%s %s" % (currency, min(starts)))
        elif ends:
            printed.append("%s %s" % (currency, max(ends)))
        if periods:
            printed.append(periods[0])
        job["salary"] = " ".join(part for part in printed if part).strip()

def crawl(ctx):
    listing_data = _next_data(ctx.fetch(SITE))
    for item in _listing_items(listing_data):
        url = item.get("url")
        if not url:
            continue

        detail_data = _next_data(ctx.fetch(url))
        api_data = detail_data.get("props", {}).get("pageProps", {}).get("apiData", {})
        job_post = api_data.get("jobPost") or {}

        description_parts = []
        desc = job_post.get("description") or {}
        if isinstance(desc, dict):
            for key in ("company", "role"):
                text = _html_to_text(desc.get(key))
                if text:
                    description_parts.append(text)
            for key, value in desc.items():
                if key not in ("company", "role"):
                    text = _html_to_text(value)
                    if text:
                        description_parts.append(text)
        elif desc:
            text = _html_to_text(desc)
            if text:
                description_parts.append(text)

        job = {
            "url": job_post.get("url") or url,
            "title": (job_post.get("name") or item.get("name") or "").strip(),
            "company": job_post.get("companyName") or "Blue Robotics",
            "location": _primary_location(job_post, item),
            "description": "\n\n".join(description_parts),
            "source": SITE,
            "external_id": job_post.get("uuid") or item.get("id"),
        }

        locations = _all_locations(job_post, item)
        if locations:
            job["locations"] = locations

        department = job_post.get("department") or item.get("department") or {}
        if department.get("name"):
            job["department"] = department.get("name")

        employment = job_post.get("employmentType") or {}
        if employment.get("id"):
            job["employment_type"] = employment.get("id")
        elif employment.get("label"):
            job["employment_type"] = employment.get("label")

        workplace = _workplace_type(job_post, item)
        if workplace:
            job["workplace_type"] = workplace

        if job_post.get("createdOn"):
            job["posted_at"] = job_post.get("createdOn")

        _apply_pay(job, job_post)

        yield {k: v for k, v in job.items() if v not in (None, "", [])}
