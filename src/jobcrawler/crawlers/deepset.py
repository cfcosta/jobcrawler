
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/deepsetai?includeCompensation=true"
EXTRA_HOSTS = []

def _text_from_html(html):
    if not html:
        return None
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article|ul|ol)>", "\n", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def _locations(job):
    seen = set()
    out = []
    for value in [job.get("location")]:
        value = _clean(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    for loc in job.get("secondaryLocations") or []:
        value = _clean(loc.get("location") if isinstance(loc, dict) else loc)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out or None

def _compensation_fields(job):
    comp = job.get("compensation") or {}
    fields = {}
    summary = _clean(comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"))
    if summary:
        fields["salary"] = summary

    components = comp.get("summaryComponents") or []
    if len(components) == 1 and isinstance(components[0], dict):
        item = components[0]
        min_value = item.get("minValue")
        max_value = item.get("maxValue")
        currency = _clean(item.get("currencyCode"))
        period = _clean(item.get("interval"))
        if min_value is not None:
            fields["salary_min"] = min_value
        if max_value is not None:
            fields["salary_max"] = max_value
        if currency:
            fields["salary_currency"] = currency
        if period:
            fields["salary_period"] = period
    return fields

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for job in data.get("jobs", []):
        description = _clean(job.get("descriptionPlain")) or _text_from_html(job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "deepset",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        optional_map = {
            "employment_type": job.get("employmentType"),
            "workplace_type": job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
            "department": job.get("department") or job.get("team"),
            "posted_at": job.get("publishedAt"),
            "external_id": job.get("id"),
            "apply_url": job.get("applyUrl"),
        }
        for key, value in optional_map.items():
            value = _clean(value)
            if value is not None:
                item[key] = value

        item.update(_compensation_fields(job))
        yield item
