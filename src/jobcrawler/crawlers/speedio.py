
import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

SITE = "https://apigw.solides.com.br/jobs/v3/home/vacancy?take=12&slug=speediojobs&title=&locations=&page=1"
EXTRA_HOSTS = []


def _page_url(page):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _text_from_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location(job):
    city = (job.get("city") or {}).get("name")
    state = job.get("state") or {}
    state_code = state.get("code") or state.get("name")
    country = ((job.get("address") or {}).get("country") or {}).get("name")
    parts = [p for p in [city, state_code, country] if p]
    return ", ".join(parts) if parts else None


def _workplace_type(job):
    value = job.get("jobType")
    if value:
        return str(value)
    if job.get("homeOffice") is True:
        return "remoto"
    if job.get("homeOffice") is False:
        return "presencial"
    return None


def _employment_type(job):
    contracts = job.get("recruitmentContractType") or []
    names = [c.get("name") for c in contracts if isinstance(c, dict) and c.get("name")]
    return ", ".join(names) if names else None


def _department(job):
    areas = job.get("occupationAreas") or []
    names = [a.get("name") for a in areas if isinstance(a, dict) and a.get("name")]
    return ", ".join(names) if names else None


def _add_salary(out, job):
    salary = job.get("salary") or {}
    if not salary.get("showRangeToApplicant"):
        return
    min_value = salary.get("initialRange")
    max_value = salary.get("finalRange")
    if min_value is None and max_value is None:
        return
    if min_value is not None:
        out["salary_min"] = float(min_value)
    if max_value is not None:
        out["salary_max"] = float(max_value)
    out["salary_currency"] = "BRL"
    out["salary_period"] = "monthly"
    if min_value is not None and max_value is not None:
        out["salary"] = "R$ %s - R$ %s" % (min_value, max_value)
    elif max_value is not None:
        out["salary"] = "Até R$ %s" % max_value
    elif min_value is not None:
        out["salary"] = "A partir de R$ %s" % min_value


def crawl(ctx):
    page = 1
    total_pages = None

    while total_pages is None or page <= total_pages:
        url = _page_url(page)
        payload = json.loads(ctx.fetch(url))
        data = payload.get("data") or {}
        total_pages = int(data.get("totalPages") or 1)

        for job in data.get("data") or []:
            if job.get("currentState") and job.get("currentState") != "em_andamento":
                continue

            external_id = str(job.get("id") or "").strip()
            title = (job.get("title") or "").strip()
            description = _text_from_html(job.get("description"))
            location = _location(job)

            if not (external_id and title and description and location):
                continue

            out = {
                "url": _page_url(page) + "#vacancy-" + external_id,
                "title": title,
                "company": "Speedio",
                "location": location,
                "description": description,
                "external_id": external_id,
                "source": SITE,
            }

            created = job.get("createdAt")
            if created:
                out["posted_at"] = created

            workplace_type = _workplace_type(job)
            if workplace_type:
                out["workplace_type"] = workplace_type

            employment_type = _employment_type(job)
            if employment_type:
                out["employment_type"] = employment_type

            department = _department(job)
            if department:
                out["department"] = department

            _add_salary(out, job)
            yield out

        page += 1
