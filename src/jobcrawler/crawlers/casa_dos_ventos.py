
import json
import re
import html

SITE = "https://casadosventos.gupy.io/"
EXTRA_HOSTS = []

def _strip_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h1|h2|h3|h4|ul|ol)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*(p|div|li|h1|h2|h3|h4|ul|ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))

def _location_from_summary(job):
    workplace = job.get("workplace") or {}
    address = workplace.get("address") or {}
    parts = []
    for key in ("city", "stateShortName", "country"):
        value = address.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or None

def _location_from_detail(job):
    parts = []
    for key in ("addressCity", "addressStateShortName", "addressCountry"):
        value = job.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) or None

def _employment_type(value):
    mapping = {
        "vacancy_type_effective": "Efetivo",
        "vacancy_type_internship": "Estágio",
        "vacancy_type_talent_pool": "Banco de talentos",
        "vacancy_type_temporary": "Temporário",
        "vacancy_type_trainee": "Trainee",
        "vacancy_type_apprentice": "Aprendiz",
        "vacancy_legal_entity": "Pessoa jurídica",
    }
    return mapping.get(value) or value

def _workplace_type(value):
    mapping = {
        "on-site": "on-site",
        "remote": "remote",
        "hybrid": "hybrid",
    }
    return mapping.get(value) or value

def _first(values):
    for value in values:
        if value is not None and value != "":
            return value
    return None

def crawl(ctx):
    root = ctx.fetch(SITE)
    data = _next_data(root)
    page_props = (data.get("props") or {}).get("pageProps") or {}
    jobs = page_props.get("jobs") or []

    for item in jobs:
        job_id = item.get("id")
        if not job_id:
            continue

        url = SITE.rstrip("/") + "/jobs/" + str(job_id) + "?jobBoardSource=gupy_public_page"
        detail_page = ctx.fetch(url)
        detail_data = _next_data(detail_page)
        detail_props = (detail_data.get("props") or {}).get("pageProps") or {}
        detail_job = detail_props.get("job") or {}

        title = _first([detail_job.get("name"), item.get("title")])
        location = _first([_location_from_detail(detail_job), _location_from_summary(item), "Brasil"])

        sections = []
        section_fields = (
            ("Descrição da vaga", "description"),
            ("Responsabilidades e atribuições", "responsibilities"),
            ("Requisitos e qualificações", "prerequisites"),
            ("Informações adicionais", "additionalInformation"),
        )
        for heading, field in section_fields:
            text = _strip_html(detail_job.get(field))
            if text:
                sections.append(heading + "\n" + text)
        description = "\n\n".join(sections).strip()

        if not description:
            match = re.search(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', detail_page, re.S)
            if match:
                try:
                    jsonld = json.loads(html.unescape(match.group(1)))
                    description = _strip_html(jsonld.get("description")) or ""
                except Exception:
                    description = ""

        record = {
            "url": url,
            "title": title,
            "company": "Casa dos Ventos",
            "location": location,
            "description": description,
            "external_id": str(job_id),
            "apply_url": url,
            "source": SITE,
        }

        department = item.get("department")
        if department:
            record["department"] = department

        employment = _employment_type(_first([detail_job.get("jobType"), item.get("type")]))
        if employment:
            record["employment_type"] = employment

        workplace_item = item.get("workplace") or {}
        workplace = _workplace_type(_first([detail_job.get("workplaceType"), workplace_item.get("workplaceType")]))
        if workplace:
            record["workplace_type"] = workplace

        posted_at = detail_job.get("publishedAt")
        if posted_at:
            record["posted_at"] = posted_at

        expires_at = _first([detail_job.get("expiresAt"), detail_job.get("registerEndDate")])
        if expires_at:
            record["expires_at"] = expires_at

        yield record
