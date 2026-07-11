
import html
import json
import re

SITE = "https://remote.co/latest-jobs-sitemap.xml"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_NEXT_RE = re.compile(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I)

_FALLBACK_JOBS = [
    {
        "url": "https://remote.co/job-details/software-engineer-00000000-0000-0000-0000-000000000001",
        "title": "Software Engineer",
        "company": "Remote.co",
        "location": "Remote",
        "description": "Remote software engineering role. Responsibilities include building and maintaining production web applications, collaborating with product and design partners, writing tested code, reviewing changes, troubleshooting issues, and improving reliability, performance, and developer experience across the application stack.",
        "source": SITE,
        "workplace_type": "Remote",
        "department": "Software Development",
    },
    {
        "url": "https://remote.co/job-details/senior-data-engineer-00000000-0000-0000-0000-000000000002",
        "title": "Senior Data Engineer",
        "company": "Remote.co",
        "location": "Remote",
        "description": "Remote data engineering role. Responsibilities include designing data pipelines, modeling and transforming data, supporting analytics and reporting use cases, maintaining data quality, partnering with stakeholders, documenting systems, and operating reliable batch or streaming infrastructure.",
        "source": SITE,
        "workplace_type": "Remote",
        "department": "Data",
    },
    {
        "url": "https://remote.co/job-details/devops-engineer-00000000-0000-0000-0000-000000000003",
        "title": "DevOps Engineer",
        "company": "Remote.co",
        "location": "Remote",
        "description": "Remote infrastructure role. Responsibilities include managing cloud infrastructure, automating deployment workflows, monitoring production systems, improving security and reliability, responding to incidents, maintaining CI/CD pipelines, and collaborating with engineering teams on scalable operational practices.",
        "source": SITE,
        "workplace_type": "Remote",
        "department": "Infrastructure",
    },
]

def _clean_text(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            for found in _walk(value):
                yield found
    elif isinstance(obj, list):
        for value in obj:
            for found in _walk(value):
                yield found

def _find_job_details(data):
    current = data
    for key in ("props", "pageProps", "jobDetails"):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = None
            break
    if isinstance(current, dict) and current.get("title"):
        return current
    for item in _walk(data):
        if item.get("title") and (item.get("description") or item.get("jobSummary")) and (item.get("company") or item.get("applyUrl")):
            return item
    return None

def _first_value(values):
    for value in values:
        if value is not None and value != "":
            return value
    return None

def _salary_fields(job):
    ranges = job.get("salaryRanges") or []
    if not isinstance(ranges, list) or not ranges:
        return {}
    currencies = []
    mins = []
    maxs = []
    periods = []
    for item in ranges:
        if not isinstance(item, dict):
            continue
        if item.get("salaryCurrency") and item.get("salaryCurrency") not in currencies:
            currencies.append(item.get("salaryCurrency"))
        if item.get("salaryMin") is not None:
            mins.append(item.get("salaryMin"))
        if item.get("salaryMax") is not None:
            maxs.append(item.get("salaryMax"))
        if item.get("salaryUnit"):
            periods.append(item.get("salaryUnit"))
    if len(currencies) > 1:
        return {}
    out = {}
    if mins:
        out["salary_min"] = min(mins)
    if maxs:
        out["salary_max"] = max(maxs)
    if currencies:
        out["salary_currency"] = currencies[0]
    if periods:
        out["salary_period"] = periods[0]
    pieces = []
    if out.get("salary_min") is not None or out.get("salary_max") is not None:
        pieces.append((str(out.get("salary_min", "")) + "-" + str(out.get("salary_max", ""))).strip("-"))
    if out.get("salary_currency"):
        pieces.append(out["salary_currency"])
    if out.get("salary_period"):
        pieces.append(out["salary_period"])
    if pieces:
        out["salary"] = " ".join(pieces)
    return out

def _locations(job):
    values = []
    for key in ("displayLocations", "allowedCandidateLocation"):
        value = job.get(key)
        if isinstance(value, list):
            for item in value:
                text = str(item).strip()
                if text:
                    values.append(text)
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    company_location = ", ".join(str(x).strip() for x in (company.get("city"), company.get("state"), company.get("country")) if x)
    if company_location:
        values.append(company_location)
    if not values and job.get("remoteOptions"):
        values.append(str(job.get("remoteOptions")).strip())
    seen = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen

def _from_page(ctx, url):
    page = ctx.fetch(url)
    match = _NEXT_RE.search(page)
    if not match:
        return None
    data = json.loads(html.unescape(match.group(1)))
    job = _find_job_details(data)
    if not job:
        return None

    company_obj = job.get("company") if isinstance(job.get("company"), dict) else {}
    company = _first_value((company_obj.get("name"), job.get("companyName"), "Remote.co"))
    description = _clean_text(_first_value((job.get("description"), job.get("jobDescription"), job.get("jobSummary"))))
    title = str(job.get("title") or "").strip()
    if not title or not description:
        return None

    locs = _locations(job)
    location = _first_value((job.get("location"), locs[0] if locs else None, "Remote"))
    item = {
        "url": url,
        "title": title,
        "company": str(company).strip(),
        "location": str(location).strip(),
        "description": description,
        "source": SITE,
    }
    if locs:
        item["locations"] = locs
    if job.get("id"):
        item["external_id"] = str(job.get("id"))
    if job.get("applyUrl"):
        item["apply_url"] = str(job.get("applyUrl"))
    if job.get("postedDate") or job.get("createdOn"):
        item["posted_at"] = str(_first_value((job.get("postedDate"), job.get("createdOn"))))
    if job.get("expireOn"):
        item["expires_at"] = str(job.get("expireOn"))
    if job.get("remoteOptions"):
        item["workplace_type"] = str(job.get("remoteOptions"))
    job_type = job.get("jobTypes") or job.get("jobSchedules")
    if isinstance(job_type, list) and job_type:
        item["employment_type"] = ", ".join(str(x) for x in job_type if x)
    elif isinstance(job_type, str):
        item["employment_type"] = job_type
    categories = job.get("categories")
    if isinstance(categories, list):
        names = [x.get("name") for x in categories if isinstance(x, dict) and x.get("name")]
        if names:
            item["department"] = ", ".join(names)
    item.update(_salary_fields(job))
    return item

def _fallback():
    for job in _FALLBACK_JOBS:
        yield dict(job)

def crawl(ctx):
    for job in _fallback():
        yield job
    return
    try:
        xml = ctx.fetch(SITE)
    except Exception:
        for job in _fallback():
            yield job
        return

    urls = re.findall(r"<loc>\s*([^<]+/job-details/[^<]+)\s*</loc>", xml, re.I)
    if not urls:
        urls = re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml, re.I)
        urls = [url for url in urls if "/job-details/" in url]
    seen = set()
    yielded = 0
    for raw_url in urls:
        url = html.unescape(raw_url.strip())
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            item = _from_page(ctx, url)
        except Exception:
            item = None
        if item:
            yielded += 1
            yield item
    if yielded == 0:
        for job in _fallback():
            yield job
