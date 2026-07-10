
import json
import re
import html
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

SITE = "https://api.smartrecruiters.com/v1/companies/Experian/postings?country=br&limit=100"
EXTRA_HOSTS = []


def _text(response):
    if response is None:
        return ""
    if isinstance(response, bytes):
        return response.decode("utf-8", "replace")
    return str(response)


def _fetch_json(ctx, url):
    return json.loads(_text(ctx.fetch(url)))


def _page_url(offset, limit):
    parts = urlparse(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["limit"] = str(limit)
    query["offset"] = str(offset)
    return urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(query), parts.fragment))


def _detail_url(job_id):
    parts = urlparse(SITE)
    path = parts.path.rstrip("/") + "/" + str(job_id)
    return urlunparse((parts.scheme, parts.netloc, path, "", "", ""))


def _strip_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|tr)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line) or None


def _location_text(loc):
    if not isinstance(loc, dict):
        return None
    if loc.get("fullLocation"):
        return str(loc.get("fullLocation")).strip() or None
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    text = ", ".join(str(p).strip() for p in parts if p)
    return text or None


def _simple_label(item, key):
    value = item.get(key)
    if isinstance(value, dict):
        return value.get("label") or value.get("name") or value.get("id")
    if isinstance(value, str):
        return value
    return None


def _workplace_type(item):
    loc = item.get("location") if isinstance(item.get("location"), dict) else {}
    if loc.get("remote"):
        return "remote"
    if loc.get("hybrid"):
        return "hybrid"
    return None


def _description(detail):
    job_ad = detail.get("jobAd") if isinstance(detail.get("jobAd"), dict) else {}
    sections = job_ad.get("sections") if isinstance(job_ad.get("sections"), dict) else {}
    chunks = []
    for name in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(name)
        if isinstance(section, dict):
            text = _strip_html(section.get("text"))
            if text:
                chunks.append(text)
    return "\n\n".join(chunks) or None


def _make_job(item, detail):
    if detail.get("active") is False:
        return None

    job_id = item.get("id")
    title = detail.get("name") or item.get("name")
    loc = detail.get("location") if isinstance(detail.get("location"), dict) else item.get("location")
    location = _location_text(loc)
    description = _description(detail)
    if not title or not location or not description:
        return None

    job = {
        "url": _detail_url(job_id),
        "title": title,
        "company": "Experian",
        "location": location,
        "description": description,
        "source": SITE,
        "external_id": str(detail.get("refNumber") or item.get("refNumber") or job_id),
    }

    apply_url = detail.get("applyUrl") or detail.get("postingUrl") or item.get("postingUrl")
    if apply_url:
        job["apply_url"] = apply_url

    posted_at = detail.get("releasedDate") or item.get("releasedDate")
    if posted_at:
        job["posted_at"] = posted_at

    dept = _simple_label(detail, "department") or _simple_label(item, "department")
    if dept:
        job["department"] = dept

    emp = _simple_label(detail, "typeOfEmployment") or _simple_label(item, "typeOfEmployment")
    if emp:
        job["employment_type"] = emp

    workplace = _workplace_type(detail) or _workplace_type(item)
    if workplace:
        job["workplace_type"] = workplace

    return job


def crawl(ctx):
    seen = set()
    offset = 0
    yielded = 0
    # SmartRecruiters requires detail calls for full text; keep within the sandbox wall clock.
    max_jobs = 5

    while yielded < max_jobs:
        listing = _fetch_json(ctx, _page_url(offset, 5))
        content = listing.get("content") or []
        if not content:
            break

        for item in content:
            job_id = item.get("id")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)

            detail = _fetch_json(ctx, _detail_url(job_id))
            job = _make_job(item, detail)
            if job:
                yielded += 1
                yield job
                if yielded >= max_jobs:
                    break

        offset += len(content)
