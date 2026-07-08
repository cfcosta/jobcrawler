
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|section|article|tr|td|th)[^>]*>", re.I)

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None

def _first_text(value):
    if value is None:
        return None
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(parts) if parts else None
    text = str(value).strip()
    return text or None

def _salary(job):
    min_v = job.get("salaryMin")
    max_v = job.get("salaryMax")
    if min_v is None and max_v is None:
        return None
    pieces = []
    currency = _first_text(job.get("salaryCurrency"))
    if currency:
        pieces.append(currency)
    if min_v is not None and max_v is not None:
        pieces.append("%s-%s" % (min_v, max_v))
    elif min_v is not None:
        pieces.append("from %s" % min_v)
    elif max_v is not None:
        pieces.append("up to %s" % max_v)
    period = _first_text(job.get("salaryPeriod"))
    if period:
        pieces.append(period)
    return " ".join(pieces) or None

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        title = _first_text(job.get("jobTitle") or job.get("title"))
        url = _first_text(job.get("url"))
        company = _first_text(job.get("companyName") or job.get("company"))
        location = _first_text(job.get("jobGeo") or job.get("location"))
        description = _clean_html(job.get("jobDescription") or job.get("description"))
        if not (title and url and company and location and description):
            continue
        if len(description) < 200:
            continue

        item = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": _first_text(job.get("jobType")),
            "department": _first_text(job.get("jobIndustry") or job.get("department")),
            "posted_at": _first_text(job.get("pubDate") or job.get("publishedAt") or job.get("datePosted")),
            "external_id": _first_text(job.get("id") or job.get("jobSlug")),
            "apply_url": _first_text(job.get("url")),
            "salary": _salary(job),
        }
        # Jobicy's filtered remote feed represents remote roles; keep the source wording when present.
        if location and "remote" in location.lower():
            optional["workplace_type"] = "Remote"
        for key, value in optional.items():
            if value:
                item[key] = value
        yield item
