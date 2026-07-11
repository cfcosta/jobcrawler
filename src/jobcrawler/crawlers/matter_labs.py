
import json
import re
from html import unescape

SITE = 'https://api.ashbyhq.com/posting-api/job-board/matter-labs?includeCompensation=true'
EXTRA_HOSTS = []

COMPANY = "Matter Labs"


def _clean_html(html):
    if not html:
        return None
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\\n{3,}", "\n\n", re.sub(r"[ \\t]+", " ", text)).strip() or None


def _plain_description(job):
    desc = job.get("descriptionPlain")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    return _clean_html(job.get("descriptionHtml"))


def _locations(job):
    values = []
    primary = job.get("location")
    if isinstance(primary, str) and primary.strip():
        values.append(primary.strip())
    for secondary in job.get("secondaryLocations") or []:
        loc = secondary.get("location") if isinstance(secondary, dict) else secondary
        if isinstance(loc, str) and loc.strip() and loc.strip() not in values:
            values.append(loc.strip())
    return values


def _add_compensation(out, job):
    comp = job.get("compensation") or {}
    printed = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if isinstance(printed, str) and printed.strip():
        out["salary"] = printed.strip()

    components = comp.get("summaryComponents") or []
    currencies = set()
    mins = []
    maxs = []
    periods = []
    for component in components:
        if not isinstance(component, dict):
            continue
        currency = component.get("currencyCode")
        if currency:
            currencies.add(currency)
        if component.get("minValue") is not None:
            mins.append(component.get("minValue"))
        if component.get("maxValue") is not None:
            maxs.append(component.get("maxValue"))
        interval = component.get("interval")
        if interval:
            periods.append(interval)

    if len(currencies) == 1:
        out["salary_currency"] = next(iter(currencies))
        if mins:
            out["salary_min"] = min(mins)
        if maxs:
            out["salary_max"] = max(maxs)
        if periods:
            out["salary_period"] = periods[0]


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = (job.get("title") or "").strip()
        description = _plain_description(job)
        location = (job.get("location") or "").strip()
        url = job.get("jobUrl")

        if not (title and description and location and url):
            continue

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            out["locations"] = locs

        mappings = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for source_key, dest_key in mappings.items():
            value = job.get(source_key)
            if isinstance(value, str):
                value = value.strip()
            if value is not None and value != "":
                out[dest_key] = value

        _add_compensation(out, job)
        yield out
