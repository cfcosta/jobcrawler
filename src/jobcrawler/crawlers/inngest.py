
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/inngest?includeCompensation=true'
EXTRA_HOSTS = []

def _response_text(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, bytes):
        return resp.decode("utf-8", "replace")
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)

def _clean_html(html):
    if not html:
        return None
    html = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    html = re.sub(r"(?i)<br\\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|li|h[1-6]|section|article)>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"\\n\\s*\\n+", "\n\n", re.sub(r"[ \\t]+", " ", text)).strip() or None

def _compact(value):
    return value if value not in (None, "") else None

def crawl(ctx):
    raw = _response_text(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = _compact(job.get("title"))
        description = _compact(job.get("descriptionPlain")) or _clean_html(job.get("descriptionHtml"))
        url = _compact(job.get("jobUrl")) or SITE
        location = _compact(job.get("location"))

        if not (title and description and location):
            continue

        locations = []
        if location:
            locations.append(location)
        for secondary in job.get("secondaryLocations") or []:
            loc = _compact(secondary.get("location") if isinstance(secondary, dict) else secondary)
            if loc and loc not in locations:
                locations.append(loc)

        item = {
            "url": url,
            "title": title,
            "company": "Inngest",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _compact(job.get("id")),
            "apply_url": _compact(job.get("applyUrl")),
            "department": _compact(job.get("department")),
            "employment_type": _compact(job.get("employmentType")),
            "workplace_type": _compact(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "posted_at": _compact(job.get("publishedAt")),
        }
        if len(locations) > 1:
            item["locations"] = locations

        compensation = job.get("compensation") or {}
        summary = compensation.get("scrapeableCompensationSalarySummary") or compensation.get("compensationTierSummary")
        if summary:
            item["salary"] = summary
        for component in compensation.get("summaryComponents") or []:
            if not isinstance(component, dict):
                continue
            currency = component.get("currencyCode")
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            interval = component.get("interval")
            if currency and (min_value is not None or max_value is not None):
                item["salary_currency"] = currency
                if min_value is not None:
                    item["salary_min"] = min_value
                if max_value is not None:
                    item["salary_max"] = max_value
                if interval:
                    item["salary_period"] = interval
                break

        yield {k: v for k, v in item.items() if v is not None}
