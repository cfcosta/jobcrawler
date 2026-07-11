
import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://remote.com/jobs/all?query=engineer&page=1"
EXTRA_HOSTS = []

Job = dict
MAX_JOBS = 12

def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response


def _strip_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _next_rsc_text(page):
    chunks = []
    for script in re.findall(r"<script[^>]*>(.*?)</script>", page, flags=re.S):
        if "self.__next_f.push" not in script:
            continue
        m = re.search(r"self\.__next_f\.push\((.*)\)\s*$", script, flags=re.S)
        if not m:
            continue
        try:
            payload = json.loads(m.group(1))
        except Exception:
            continue
        if len(payload) > 1 and isinstance(payload[1], str):
            chunks.append(payload[1])
    return "".join(chunks)


def _brace_object(text, start):
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _json_after_key(text, key):
    pos = text.find(key)
    if pos == -1:
        return None
    start = text.find("{", pos)
    if start == -1:
        return None
    raw = _brace_object(text, start)
    return json.loads(raw) if raw else None


def _jobs_data(page):
    rsc = _next_rsc_text(page)
    return _json_after_key(rsc, '"jobsData"')


def _jobposting(page):
    rsc = _next_rsc_text(page)
    pos = rsc.find('"@type":"JobPosting"')
    if pos == -1:
        pos = rsc.find('"@type":["JobPosting"')
    if pos == -1:
        return None
    start = rsc.rfind("{", 0, pos)
    raw = _brace_object(rsc, start) if start != -1 else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _page_url(page_number):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _detail_url(job):
    company = (job.get("companyProfile") or {}).get("slug")
    slug = job.get("slug")
    if not company or not slug:
        return None
    return "https://remote.com/jobs/%s/%s" % (company, slug)


def _locations(job):
    hiring = job.get("hiringLocation") or {}
    included = hiring.get("includedLocations") or []
    names = []
    for item in included:
        value = (item or {}).get("value") or {}
        name = value.get("name")
        if name:
            names.append(name)
    if names:
        return names[0], names
    htype = hiring.get("type")
    if htype == "global":
        return "Global", ["Global"]
    return "Remote", ["Remote"]


def _department_name(job):
    department = job.get("department")
    if isinstance(department, dict):
        return department.get("name")
    return department


def _compensation_fields(job):
    comp = job.get("compensation") or {}
    currency = comp.get("currency") or {}
    out = {}
    if comp.get("minimum") is not None:
        out["salary_min"] = comp.get("minimum") / 100.0
    if comp.get("maximum") is not None:
        out["salary_max"] = comp.get("maximum") / 100.0
    if currency.get("code"):
        out["salary_currency"] = currency.get("code")
    if comp.get("frequency"):
        out["salary_period"] = comp.get("frequency")
    return out


def _clean_job_kwargs(kwargs):
    return {k: v for k, v in kwargs.items() if v is not None and v != ""}


def crawl(ctx):
    first = _fetch_text(ctx, SITE)
    data = _jobs_data(first)
    if not data:
        return

    total_pages = int(data.get("totalPages") or 1)
    seen = set()
    yielded = 0

    for page_number in range(1, total_pages + 1):
        if page_number == 1:
            page_data = data
        else:
            page_data = _jobs_data(_fetch_text(ctx, _page_url(page_number)))
            if not page_data:
                continue

        for job in page_data.get("jobs") or []:
            if job.get("status") and job.get("status") != "published":
                continue
            url = _detail_url(job)
            if not url or url in seen:
                continue
            seen.add(url)

            detail = _fetch_text(ctx, url)
            posting = _jobposting(detail) or {}
            description = _strip_html(posting.get("description"))
            if not description:
                continue

            company_profile = job.get("companyProfile") or {}
            location, locations = _locations(job)
            workplace = (job.get("workplaceLocation") or {}).get("type")
            kwargs = {
                "url": url,
                "title": job.get("title") or posting.get("title"),
                "company": company_profile.get("name"),
                "location": location,
                "locations": locations,
                "description": description,
                "source": SITE,
                "employment_type": job.get("employmentType"),
                "workplace_type": workplace,
                "department": _department_name(job),
                "posted_at": job.get("publishedAt"),
                "expires_at": job.get("deadline"),
                "external_id": job.get("slug"),
                "apply_url": job.get("applyUrl"),
            }
            kwargs.update(_compensation_fields(job))
            yield Job(**_clean_job_kwargs(kwargs))
            yielded += 1
            if yielded >= MAX_JOBS:
                return
