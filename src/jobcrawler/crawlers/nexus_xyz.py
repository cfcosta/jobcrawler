
import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/nexus.xyz?includeCompensation=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|article|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line) or None


def _salary_fields(job):
    comp = job.get("compensation") or {}
    out = {}

    salary_text = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    if salary_text:
        out["salary"] = salary_text

    components = comp.get("summaryComponents") or []
    for component in components:
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is not None or max_value is not None:
            if min_value is not None:
                out["salary_min"] = min_value
            if max_value is not None:
                out["salary_max"] = max_value
            if currency:
                out["salary_currency"] = currency
            if interval:
                out["salary_period"] = interval
            break

    return out


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            loc = secondary.get("location") or secondary.get("name")
        else:
            loc = secondary
        if loc and loc not in values:
            values.append(loc)

    return values


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        title = job.get("title")
        location = job.get("location")
        url = job.get("jobUrl")

        if not (title and location and url and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Nexus",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = _locations(job)
        if locations:
            item["locations"] = locations

        field_map = {
            "id": "external_id",
            "applyUrl": "apply_url",
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
        }
        for source_key, target_key in field_map.items():
            value = job.get(source_key)
            if value:
                item[target_key] = value

        item.update(_salary_fields(job))
        yield item
