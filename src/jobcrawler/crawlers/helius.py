
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/helius?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "Helius"


def _clean_html(html):
    if not html:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|section)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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
    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if printed:
        out["salary"] = printed

    components = comp.get("summaryComponents") or []
    currencies = {c.get("currencyCode") for c in components if c.get("currencyCode")}
    if len(currencies) != 1:
        return

    mins = [c.get("minValue") for c in components if isinstance(c.get("minValue"), (int, float))]
    maxs = [c.get("maxValue") for c in components if isinstance(c.get("maxValue"), (int, float))]
    if mins:
        out["salary_min"] = min(mins)
    if maxs:
        out["salary_max"] = max(maxs)
    if mins or maxs:
        out["salary_currency"] = next(iter(currencies))
        for c in components:
            if c.get("interval"):
                out["salary_period"] = c.get("interval")
                break


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        title = job.get("title")
        url = job.get("jobUrl")
        location = job.get("location")
        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "apply_url": job.get("applyUrl"),
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        _add_compensation(out, job)

        yield {k: v for k, v in out.items() if v is not None and v != ""}
