
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://pagseguro.gupy.io/"
EXTRA_HOSTS = []

def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))

def _html_to_text(value):
    if not value:
        return None
    text = str(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|section|article|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|ul|ol|section|article|h[1-6])[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _location_from_listing(job):
    workplace = job.get("workplace") or {}
    address = workplace.get("address") or {}
    pieces = []
    workplace_type = workplace.get("workplaceType")
    if workplace_type:
        pieces.append(str(workplace_type))
    for key in ("city", "state", "country"):
        value = address.get(key)
        if value:
            pieces.append(str(value))
    return " - ".join(pieces) or "Brasil"

def _location_from_detail(job, fallback):
    pieces = []
    workplace_type = job.get("workplaceType")
    if workplace_type:
        pieces.append(str(workplace_type))
    for key in ("addressCity", "addressState", "addressCountry"):
        value = job.get(key)
        if value:
            pieces.append(str(value))
    return " - ".join(pieces) or fallback or "Brasil"

def _description(job):
    sections = []
    for label, key in (
        ("Descrição da vaga", "description"),
        ("Responsabilidades e atribuições", "responsibilities"),
        ("Requisitos e qualificações", "prerequisites"),
        ("Experiências relevantes", "relevantExperiences"),
    ):
        text = _html_to_text(job.get(key))
        if text:
            sections.append(label + "\n" + text)
    return "\n\n".join(sections).strip()

def _workplace_type(value):
    if not value:
        return None
    mapped = {
        "remote": "remote",
        "hybrid": "hybrid",
        "on-site": "on-site",
        "onsite": "on-site",
    }
    return mapped.get(str(value).lower(), str(value))

def _employment_type(value):
    if not value:
        return None
    return str(value)

def crawl(ctx):
    root = ctx.fetch(SITE)
    data = _next_data(root)
    jobs = data.get("props", {}).get("pageProps", {}).get("jobs") or []

    for item in jobs:
        department = item.get("department")
        if not str(department or "").startswith("TECNOLOGIA DA INFORMAÇÃO"):
            continue

        job_id = item.get("id")
        title = item.get("title")
        if not job_id or not title:
            continue

        url = urljoin(SITE, f"jobs/{job_id}?jobBoardSource=gupy_public_page")
        page = ctx.fetch(url)
        detail_data = _next_data(page)
        detail = detail_data.get("props", {}).get("pageProps", {}).get("job") or {}

        if detail.get("status") and detail.get("status") != "published":
            continue

        description = _description(detail)
        if not description:
            continue

        fallback_location = _location_from_listing(item)
        location = _location_from_detail(detail, fallback_location)
        workplace_type = _workplace_type(detail.get("workplaceType") or (item.get("workplace") or {}).get("workplaceType"))

        job = {
            "url": url,
            "title": detail.get("name") or title,
            "company": "PagSeguro",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
            "apply_url": url,
            "department": department,
        }

        if workplace_type:
            job["workplace_type"] = workplace_type
        employment_type = _employment_type(item.get("type") or detail.get("jobType"))
        if employment_type:
            job["employment_type"] = employment_type
        if detail.get("publishedAt"):
            job["posted_at"] = detail.get("publishedAt")
        if detail.get("expiresAt"):
            job["expires_at"] = detail.get("expiresAt")

        yield job
