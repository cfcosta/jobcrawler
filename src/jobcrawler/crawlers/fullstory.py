
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/fullstory?includeCompensation=true'
EXTRA_HOSTS = []

COMPANY = "Fullstory"


def _response_text(resp):
    if isinstance(resp, str):
        return resp
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)


def _strip_html(value):
    if not value:
        return None
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6])\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = item.get("location") if isinstance(item, dict) else item
        if loc and loc not in values:
            values.append(loc)
    return values


def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    summary = comp.get("scrapeableCompensationSalarySummary")
    if summary:
        out["salary"] = summary

    components = comp.get("summaryComponents") or []
    currencies = {c.get("currencyCode") for c in components if c.get("currencyCode")}
    if len(currencies) == 1:
        mins = [c.get("minValue") for c in components if c.get("minValue") is not None]
        maxs = [c.get("maxValue") for c in components if c.get("maxValue") is not None]
        periods = [c.get("interval") for c in components if c.get("interval")]
        if mins:
            out["salary_min"] = min(mins)
        if maxs:
            out["salary_max"] = max(maxs)
        out["salary_currency"] = next(iter(currencies))
        if periods:
            out["salary_period"] = periods[0]


def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)

    for job in data.get("jobs", []):
        if job.get("isListed") is not True:
            continue

        description = job.get("descriptionPlain") or _strip_html(job.get("descriptionHtml"))
        locs = _locations(job)
        location = job.get("location") or (locs[0] if locs else None)

        out = {
            "url": job.get("jobUrl"),
            "title": job.get("title"),
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
        }
        if locs:
            out["locations"] = locs
        _add_compensation(out, job)

        yield {k: v for k, v in out.items() if v is not None and v != ""}
