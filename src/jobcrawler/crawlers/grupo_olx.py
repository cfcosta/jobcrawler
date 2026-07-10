
import html
import json
import re

SITE = "https://vemsergrupoolx.gupy.io/"
EXTRA_HOSTS = []


def _next_data(page):
    match = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _strip_html(value):
    if not value:
        return None
    text = value
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    cleaned = "\n".join(lines)
    return cleaned or None


def _location_from_detail(job):
    parts = []
    city = (job.get("addressCity") or "").strip()
    state = (job.get("addressStateShortName") or job.get("addressState") or "").strip()
    country = (job.get("addressCountry") or "").strip()
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if country:
        parts.append(country)
    return ", ".join(parts) if parts else "Brasil"


def _employment_type(value):
    if value == "vacancy_type_effective":
        return "effective"
    if value == "vacancy_type_associate":
        return "associate"
    if value == "vacancy_type_talent_pool":
        return "talent_pool"
    return value or None


def _job_url(job_id):
    return SITE.rstrip("/") + "/jobs/" + str(job_id) + "?jobBoardSource=gupy_public_page"


def crawl(ctx):
    root = ctx.fetch(SITE)
    data = _next_data(root)
    list_jobs = data.get("props", {}).get("pageProps", {}).get("jobs", []) or []

    for list_job in list_jobs:
        job_id = list_job.get("id")
        if not job_id:
            continue

        url = _job_url(job_id)
        page = ctx.fetch(url)
        detail_data = _next_data(page)
        job = detail_data.get("props", {}).get("pageProps", {}).get("job", {}) or {}
        if job.get("status") and job.get("status") != "published":
            continue

        sections = []
        for field in ("description", "responsibilities", "prerequisites", "relevantExperiences", "additionalInformation"):
            text = _strip_html(job.get(field))
            if text:
                sections.append(text)
        description = "\n\n".join(sections)
        if not description:
            continue

        title = job.get("name") or list_job.get("title")
        if not title:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Grupo OLX",
            "location": _location_from_detail(job),
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id") or job_id),
            "apply_url": url,
        }

        workplace_type = job.get("workplaceType") or (list_job.get("workplace") or {}).get("workplaceType")
        if workplace_type:
            item["workplace_type"] = workplace_type

        employment_type = _employment_type(job.get("jobType") or list_job.get("type"))
        if employment_type:
            item["employment_type"] = employment_type

        department = list_job.get("department")
        if department:
            item["department"] = department

        if job.get("publishedAt"):
            item["posted_at"] = job.get("publishedAt")
        if job.get("expiresAt"):
            item["expires_at"] = job.get("expiresAt")

        yield item
