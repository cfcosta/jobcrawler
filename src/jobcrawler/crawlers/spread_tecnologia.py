
import json
import re
import html

SITE = "https://spread.gupy.io/"
EXTRA_HOSTS = []

COMPANY = "Spread Tecnologia"
MAX_DETAIL_FETCHES = 12


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response


def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h1|h2|h3|h4|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _clean_title(title):
    title = re.sub(r"\s+", " ", (title or "")).strip()
    cleaned = re.sub(r"\s+-\s+RHA[A-Z0-9]*\d+\s*$", "", title, flags=re.I).strip()
    return cleaned or title


def _location_from_job(job, board_job):
    parts = []
    for key in ("addressCity", "addressStateShortName", "addressState", "addressCountry"):
        value = (job.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    if not parts:
        address = (board_job.get("workplace") or {}).get("address") or {}
        for key in ("city", "stateShortName", "state", "country"):
            value = (address.get(key) or "").strip()
            if value and value not in parts:
                parts.append(value)
    if parts:
        return ", ".join(parts)
    return (job.get("addressLine") or "Brasil").strip()


def _workplace_type(value):
    mapping = {
        "remote": "remote",
        "on-site": "on-site",
        "hybrid": "hybrid",
    }
    return mapping.get((value or "").strip().lower())


def _employment_type(value):
    mapping = {
        "vacancy_type_effective": "CLT",
        "vacancy_legal_entity": "Pessoa jurídica",
        "vacancy_type_talent_pool": "Banco de talentos",
        "vacancy_type_apprentice": "Aprendiz",
        "vacancy_type_internship": "Estágio",
        "vacancy_type_temporary": "Temporário",
    }
    return mapping.get(value)


def _description(job):
    sections = []
    for key in ("description", "responsibilities", "prerequisites", "relevantExperiences"):
        text = _html_to_text(job.get(key))
        if text:
            sections.append(text)
    return "\n\n".join(sections).strip()


def _make_item(job, board_job, url, job_id):
    title = _clean_title(job.get("name") or board_job.get("title"))
    description = _description(job)
    if not title or not description:
        return None

    item = {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": _location_from_job(job, board_job),
        "description": description,
        "source": SITE,
        "external_id": str(job.get("id") or job_id),
        "apply_url": url,
    }

    department = board_job.get("department")
    if department:
        item["department"] = department

    posted_at = job.get("publishedAt")
    if posted_at:
        item["posted_at"] = posted_at

    expires_at = job.get("expiresAt") or job.get("registerEndDate")
    if expires_at:
        item["expires_at"] = expires_at

    employment_type = _employment_type(job.get("jobType") or board_job.get("type"))
    if employment_type:
        item["employment_type"] = employment_type

    workplace_type = _workplace_type(job.get("workplaceType") or ((board_job.get("workplace") or {}).get("workplaceType")))
    if workplace_type:
        item["workplace_type"] = workplace_type

    return item


def crawl(ctx):
    root = _fetch_text(ctx, SITE)
    data = _next_data(root)
    build_id = data.get("buildId")
    page_props = data.get("props", {}).get("pageProps", {})
    board_jobs = page_props.get("jobs") or []

    fetched = 0
    for board_job in board_jobs:
        if fetched >= MAX_DETAIL_FETCHES:
            break
        job_id = board_job.get("id")
        if not job_id or not build_id:
            continue

        url = SITE.rstrip("/") + "/jobs/%s?jobBoardSource=gupy_public_page" % job_id
        data_url = SITE.rstrip("/") + "/_next/data/%s/pt/jobs/%s.json?jobBoardSource=gupy_public_page" % (build_id, job_id)
        detail_data = json.loads(_fetch_text(ctx, data_url))
        job = detail_data.get("pageProps", {}).get("job") or {}
        fetched += 1

        item = _make_item(job, board_job, url, job_id)
        if item:
            yield item
