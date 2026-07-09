import html
import json
import re
from urllib.parse import quote

SITE = "https://www.wearedevelopers.com/en/jobs/s/rust"
EXTRA_HOSTS = ["wad-api.wearedevelopers.com"]

API_BASE = "https://wad-api.wearedevelopers.com/api"
LIST_URL_PREFIX = API_BASE + "/v2/jobs?skills[]=18&job_listings_page="
MAX_JOBS = 36


def _strip_html(value):
    if value is None:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_non_empty(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _canonical_url(job):
    return f"https://www.wearedevelopers.com/en/jobs/ext/{job.get('id')}/{job.get('slug') or ''}"


def _detail_url(job):
    slug = quote(job.get("slug") or "", safe="")
    return f"{API_BASE}/v2/jobs/details?job_slug={slug}&job_id={job.get('id')}&external=true"


def _unwrap_detail(obj):
    if not isinstance(obj, dict):
        return {}
    for key in ("data", "job", "job_listing", "jobListing", "listing"):
        value = obj.get(key)
        if isinstance(value, dict) and (
            "description" in value
            or "candidate_description" in value
            or "conditions_description" in value
        ):
            return value
    return obj


def _description(details):
    parts = []
    for key in (
        "employer_description",
        "description",
        "candidate_description",
        "conditions_description",
    ):
        text = _strip_html(details.get(key))
        if text:
            parts.append(text)
    if parts:
        return "\n\n".join(parts).strip()
    return None


def _location(details, listing):
    location = _first_non_empty(details.get("location"), listing.get("location"))
    if location:
        return location

    for list_key in ("cities", "remote_locations"):
        values = details.get(list_key)
        if isinstance(values, list):
            names = []
            for item in values:
                if isinstance(item, dict):
                    name = _first_non_empty(
                        item.get("name"),
                        item.get("city"),
                        item.get("title"),
                        item.get("country"),
                    )
                    if name:
                        names.append(name)
                elif isinstance(item, str) and item:
                    names.append(item)
            if names:
                return ", ".join(names)

    if _first_non_empty(details.get("remote"), listing.get("remote")) is True:
        return "Remote"
    return None


def _salary_value(details, listing):
    salary = _first_non_empty(details.get("salary"), listing.get("salary"))
    if not salary:
        return None
    if isinstance(salary, dict):
        low = _first_non_empty(
            salary.get("min"), salary.get("minimum"), salary.get("from")
        )
        high = _first_non_empty(
            salary.get("max"), salary.get("maximum"), salary.get("to")
        )
        if low and high:
            return f"{low} - {high}"
        if low:
            return str(low)
        if high:
            return str(high)
        return None
    return str(salary)


def crawl(ctx):
    page = 1
    seen_pages = set()
    yielded = 0

    while page and page not in seen_pages:
        seen_pages.add(page)
        data = json.loads(ctx.fetch(f"{LIST_URL_PREFIX}{page}"))
        listings = data.get("job_listings") or []
        if not listings:
            break

        for listing in listings:
            if not listing.get("id"):
                continue

            details = {}
            try:
                details = _unwrap_detail(json.loads(ctx.fetch(_detail_url(listing))))
            except Exception:
                details = {}

            description = _description(details)
            title = _first_non_empty(details.get("title"), listing.get("title"))
            company = _first_non_empty(
                details.get("company_name"), listing.get("company_name")
            )
            location = _location(details, listing)

            if not title or not company or not location or not description:
                continue

            job = {
                "url": _canonical_url(listing),
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(listing.get("id")),
            }

            posted_at = _first_non_empty(
                details.get("last_published_at"), listing.get("last_published_at")
            )
            if posted_at:
                job["posted_at"] = posted_at

            apply_url = _first_non_empty(
                details.get("apply_url"), listing.get("job_url")
            )
            if apply_url:
                job["apply_url"] = apply_url

            employment_type = _first_non_empty(
                details.get("employment_type"), details.get("contract_type")
            )
            if employment_type:
                job["employment_type"] = employment_type

            remote = _first_non_empty(details.get("remote"), listing.get("remote"))
            if remote is True:
                job["workplace_type"] = "remote"
            elif details.get("is_hybrid") is True:
                job["workplace_type"] = "hybrid"
            elif remote is False:
                job["workplace_type"] = "on-site"

            salary = _salary_value(details, listing)
            if salary:
                job["salary"] = salary
                currency = _first_non_empty(
                    details.get("currency"), listing.get("currency")
                )
                if currency:
                    job["salary_currency"] = currency

            yield job
            yielded += 1
            if yielded >= MAX_JOBS:
                return

        pagination = data.get("pagination") or {}
        next_page = pagination.get("job_listings_next_page")
        if not next_page or next_page == page:
            break
        page = next_page
