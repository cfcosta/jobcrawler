
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://www.ycombinator.com/companies/growthbook/jobs"
EXTRA_HOSTS = []


def parse_page_data(body):
    matches = re.findall(
        r'\bdata-page\s*=\s*(["\'])(.*?)\1',
        body,
        flags=re.I | re.S,
    )
    for quote, raw in matches:
        try:
            value = json.loads(html.unescape(raw))
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def job_posting_jsonld(body):
    documents = re.findall(
        r'<script\b[^>]*\btype\s*=\s*(["\'])application/ld\+json\1[^>]*>(.*?)</script\s*>',
        body,
        flags=re.I | re.S,
    )
    candidates = []
    for quote, raw in documents:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, list):
            candidates.extend(value)
        else:
            candidates.append(value)

    for value in candidates:
        if not isinstance(value, dict):
            continue
        types = value.get("@type")
        if types == "JobPosting" or (
            isinstance(types, list) and "JobPosting" in types
        ):
            return value
    return None


def description_text(value):
    if not value:
        return None

    text = re.sub(
        r'<\s*br\s*/?\s*>',
        "\n",
        value,
        flags=re.I,
    )
    text = re.sub(
        r'<\s*li\b[^>]*>',
        "\n- ",
        text,
        flags=re.I,
    )
    text = re.sub(
        r'</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|'
        r'figcaption|figure|footer|h[1-6]|header|hr|main|ol|p|pre|'
        r'section|table|tr|ul)\b[^>]*>',
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r'<[^>]+>', "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    listing_body = ctx.fetch(SITE)
    page = parse_page_data(listing_body)
    if not page:
        return

    props = page.get("props") or {}
    company_data = props.get("company") or {}
    company = company_data.get("name") or "GrowthBook"
    postings = props.get("jobPostings") or company_data.get("jobs") or []

    seen = set()
    for listing in postings:
        relative_url = listing.get("url")
        if not relative_url:
            continue

        detail_url = urljoin(SITE, relative_url)
        if detail_url in seen:
            continue
        seen.add(detail_url)

        detail_body = ctx.fetch(detail_url)
        structured = job_posting_jsonld(detail_body) or {}
        detail_page = parse_page_data(detail_body) or {}
        detail_job = (detail_page.get("props") or {}).get("job") or {}

        title = (
            structured.get("title")
            or detail_job.get("title")
            or listing.get("title")
        )
        description = description_text(structured.get("description"))
        if not description:
            description = detail_job.get("description")
        location = listing.get("location") or detail_job.get("location")

        if not title or not description or not location:
            continue

        job = {
            "url": structured.get("url") or detail_url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = listing.get("id") or detail_job.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        apply_url = listing.get("applyUrl") or detail_job.get("applyUrl")
        if apply_url:
            job["apply_url"] = apply_url

        employment_type = (
            structured.get("employmentType")
            or listing.get("type")
        )
        if employment_type:
            job["employment_type"] = employment_type

        department = listing.get("prettyRole") or listing.get("role")
        if department:
            job["department"] = department

        posted_at = structured.get("datePosted")
        if posted_at:
            job["posted_at"] = posted_at

        if structured.get("jobLocationType") == "TELECOMMUTE":
            job["workplace_type"] = "remote"

        salary_text = listing.get("salaryRange")
        if salary_text:
            job["salary"] = salary_text

        base_salary = structured.get("baseSalary") or {}
        salary_value = base_salary.get("value") or {}
        if isinstance(salary_value, dict):
            if salary_value.get("minValue") is not None:
                job["salary_min"] = salary_value["minValue"]
            if salary_value.get("maxValue") is not None:
                job["salary_max"] = salary_value["maxValue"]

            currency = base_salary.get("currency")
            if currency:
                job["salary_currency"] = currency

            period = salary_value.get("unitText")
            if period:
                job["salary_period"] = period

        yield job
