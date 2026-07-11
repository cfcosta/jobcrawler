
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/SigNoz?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _nonempty(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _locations(job):
    values = []
    primary = _nonempty(job.get("location"))
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            loc = _nonempty(item.get("location") or item.get("name"))
        else:
            loc = _nonempty(item)
        if loc and loc not in values:
            values.append(loc)
    return values


def _salary_fields(job):
    comp = job.get("compensation") or {}
    fields = {}

    printed = _nonempty(comp.get("scrapeableCompensationSalarySummary"))
    if printed:
        fields["salary"] = printed

    components = []
    for item in comp.get("summaryComponents") or []:
        if isinstance(item, dict):
            components.append(item)
    for tier in comp.get("compensationTiers") or []:
        for item in (tier or {}).get("components") or []:
            if isinstance(item, dict):
                components.append(item)

    salary_components = [
        item for item in components
        if item.get("compensationType") == "Salary" and item.get("currencyCode")
    ]
    if salary_components:
        currencies = {item.get("currencyCode") for item in salary_components if item.get("currencyCode")}
        if len(currencies) == 1:
            item = salary_components[0]
            if item.get("minValue") is not None:
                fields["salary_min"] = item.get("minValue")
            if item.get("maxValue") is not None:
                fields["salary_max"] = item.get("maxValue")
            fields["salary_currency"] = item.get("currencyCode")
            interval = _nonempty(item.get("interval"))
            if interval:
                fields["salary_period"] = interval

    return fields


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = _nonempty(job.get("descriptionPlain")) or _clean_html(job.get("descriptionHtml"))
        url = _nonempty(job.get("jobUrl"))
        title = _nonempty(job.get("title"))
        location = _nonempty(job.get("location"))

        if not (url and title and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "SigNoz",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locs = _locations(job)
        if locs:
            item["locations"] = locs

        mappings = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for src, dst in mappings.items():
            value = _nonempty(job.get(src))
            if value:
                item[dst] = value

        item.update(_salary_fields(job))
        yield item
