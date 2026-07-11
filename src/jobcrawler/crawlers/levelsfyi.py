
import json
import re
import html
from urllib.parse import urlencode

SITE = "https://www.levels.fyi/jobs"
EXTRA_HOSTS = []

BOARD_URL = "https://www.levels.fyi/en-us/jobs"
MAX_JOBS = 8


def _next_data(text):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        return {}
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return {}


def _page_props(text):
    return _next_data(text).get("props", {}).get("pageProps", {}) or {}


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _primary_location(locations):
    if isinstance(locations, list):
        for loc in locations:
            if loc:
                return str(loc)
    elif locations:
        return str(locations)
    return None


def _salary_text(min_salary, max_salary, currency):
    if min_salary is None and max_salary is None:
        return None
    cur = currency or ""
    if min_salary is not None and max_salary is not None:
        return f"{cur} {min_salary} - {max_salary} base salary".strip()
    if min_salary is not None:
        return f"{cur} {min_salary}+ base salary".strip()
    return f"Up to {cur} {max_salary} base salary".strip()


def _job_from_detail(detail, fallback_company=None):
    company_info = detail.get("companyInfo") or {}
    locations = detail.get("locations") if isinstance(detail.get("locations"), list) else None

    job = {
        "source": SITE,
        "url": "https://www.levels.fyi/en-us/jobs?" + urlencode({"jobId": detail.get("id")}),
        "title": detail.get("title"),
        "company": company_info.get("name") or fallback_company,
        "location": _primary_location(locations),
        "description": _clean_html(detail.get("description")),
    }

    if locations:
        job["locations"] = [str(x) for x in locations if x]
    if detail.get("id") is not None:
        job["external_id"] = str(detail.get("id"))
    if detail.get("applicationUrl"):
        job["apply_url"] = detail.get("applicationUrl")
    if detail.get("postingDate"):
        job["posted_at"] = detail.get("postingDate")
    if detail.get("expiryDate"):
        job["expires_at"] = detail.get("expiryDate")
    if detail.get("workArrangement"):
        job["workplace_type"] = detail.get("workArrangement")

    employment = detail.get("employmentTypes")
    if isinstance(employment, list) and employment:
        job["employment_type"] = ", ".join(str(x) for x in employment if x)
    elif employment:
        job["employment_type"] = str(employment)

    if detail.get("jobFamilySlug"):
        job["department"] = detail.get("jobFamilySlug")

    min_base = detail.get("minBaseSalary")
    max_base = detail.get("maxBaseSalary")
    currency = detail.get("baseSalaryCurrency")
    if min_base is not None:
        job["salary_min"] = min_base
    if max_base is not None:
        job["salary_max"] = max_base
    if currency:
        job["salary_currency"] = currency
    if min_base is not None or max_base is not None:
        job["salary_period"] = "year"
        salary = _salary_text(min_base, max_base, currency)
        if salary:
            job["salary"] = salary

    return {k: v for k, v in job.items() if v is not None and v != ""}


def crawl(ctx):
    props = _page_props(ctx.fetch(BOARD_URL))
    groups = props.get("initialJobsData", {}).get("results", []) or []

    seen = set()
    yielded = 0
    for group in groups:
        fallback_company = group.get("companyName")
        for summary in group.get("jobs", []) or []:
            if yielded >= MAX_JOBS:
                return
            job_id = summary.get("id")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)

            detail_url = "https://www.levels.fyi/en-us/jobs?" + urlencode({"jobId": job_id})
            detail = _page_props(ctx.fetch(detail_url)).get("initialJobDetails") or {}
            if not isinstance(detail, dict) or not detail.get("description"):
                continue

            item = _job_from_detail(detail, fallback_company=fallback_company)
            if item.get("title") and item.get("company") and item.get("location") and item.get("description"):
                yielded += 1
                yield item
