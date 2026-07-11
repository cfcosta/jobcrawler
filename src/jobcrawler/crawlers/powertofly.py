
import json
import re
import html
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

SITE = "https://powertofly.com/api/v1/jobs/search/?page=1&per_page=20&location_type=Remote&keywords=Software+Engineer&fields=id,title,headline,description,location,employment_type,published_on,display_company_name,company,custom_apply_url,levels_of_experience,skills,secondary_locations,city,state,country,salary,qualifications,is_sponsorship_available"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None

def _paged_url(page):
    parts = urlsplit(SITE)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["page"] = str(page)
    q["per_page"] = "100"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

def _place_title(obj):
    if isinstance(obj, dict):
        return obj.get("title") or obj.get("name")
    return None

def _location(job):
    parts = []
    for key in ("city", "state", "country"):
        title = _place_title(job.get(key))
        if title:
            parts.append(title)
    if parts:
        return ", ".join(parts)
    return job.get("location") or "Remote"

def _locations(job):
    values = []
    primary = _location(job)
    if primary:
        values.append(primary)
    for loc in job.get("secondary_locations") or []:
        if isinstance(loc, dict):
            title = loc.get("title") or loc.get("name")
        else:
            title = str(loc) if loc else None
        if title and title not in values:
            values.append(title)
    return values or None

def _job_url(job):
    company = job.get("company") or {}
    slug = company.get("slug") if isinstance(company, dict) else None
    jid = job.get("id")
    if slug and jid:
        return f"https://powertofly.com/jobs/detail/{slug}-{jid}"
    if jid:
        return f"https://powertofly.com/jobs/detail/{jid}"
    return SITE

def _salary_fields(out, salary):
    if not isinstance(salary, dict):
        return
    mn = salary.get("min_salary")
    mx = salary.get("max_salary")
    cur = salary.get("currency")
    period = salary.get("rate_type") or salary.get("pay_type")
    has_amount = False
    if isinstance(mn, (int, float)) and mn > 0:
        out["salary_min"] = mn
        has_amount = True
    if isinstance(mx, (int, float)) and mx > 0:
        out["salary_max"] = mx
        has_amount = True
    if cur and has_amount:
        out["salary_currency"] = cur
    if period and has_amount:
        out["salary_period"] = period

def crawl(ctx):
    page = 1
    seen = set()
    while True:
        raw = ctx.fetch(_paged_url(page))
        data = json.loads(raw)
        jobs = data.get("data") or []
        for job in jobs:
            jid = job.get("id")
            if jid in seen:
                continue
            seen.add(jid)

            desc = _clean_html(job.get("description"))
            title = job.get("title")
            company_obj = job.get("company") or {}
            company = job.get("display_company_name") or (company_obj.get("name") if isinstance(company_obj, dict) else None)
            loc = _location(job)
            if not (title and company and loc and desc):
                continue

            out = {
                "url": _job_url(job),
                "title": title,
                "company": company,
                "location": loc,
                "description": desc,
                "source": SITE,
            }

            locs = _locations(job)
            if locs and len(locs) > 1:
                out["locations"] = locs
            if job.get("employment_type"):
                out["employment_type"] = job.get("employment_type")
            if job.get("location"):
                out["workplace_type"] = job.get("location")
            if job.get("published_on"):
                out["posted_at"] = job.get("published_on")
            if jid is not None:
                out["external_id"] = str(jid)
            if job.get("custom_apply_url"):
                out["apply_url"] = job.get("custom_apply_url")

            _salary_fields(out, job.get("salary"))
            yield out

        meta = data.get("meta") or {}
        next_page = meta.get("next_page")
        if not next_page:
            break
        page = int(next_page)
