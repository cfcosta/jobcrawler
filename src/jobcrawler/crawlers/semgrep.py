
import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/semgrep?includeCompensation=true"
EXTRA_HOSTS = []


def _strip_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _clean(kwargs):
    return {k: v for k, v in kwargs.items() if v is not None and v != "" and v != []}


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)
    for item in job.get("secondaryLocations") or []:
        if isinstance(item, dict):
            loc = item.get("location") or item.get("name")
        else:
            loc = item
        if loc and loc not in values:
            values.append(loc)
    return values


def _compensation(job):
    comp = job.get("compensation") or {}
    result = {}
    if not isinstance(comp, dict) or not comp:
        return result

    if comp.get("compensationTierSummary"):
        result["salary"] = comp.get("compensationTierSummary")
    elif comp.get("summary"):
        result["salary"] = comp.get("summary")

    components = comp.get("summaryComponents") or []
    structured = []
    for component in components:
        if not isinstance(component, dict):
            continue
        currency = component.get("currencyCode") or component.get("currency")
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        interval = component.get("interval") or component.get("compensationType")
        if currency and (min_value is not None or max_value is not None):
            structured.append((currency, min_value, max_value, interval))

    currencies = {item[0] for item in structured if item[0]}
    if len(currencies) == 1 and structured:
        currency, min_value, max_value, interval = structured[0]
        result["salary_currency"] = currency
        result["salary_min"] = min_value
        result["salary_max"] = max_value
        result["salary_period"] = interval
    return result


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain") or _strip_html(posting.get("descriptionHtml"))
        url = posting.get("jobUrl")
        title = posting.get("title")
        location = posting.get("location")
        if not (url and title and location and description):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Semgrep",
            "location": location,
            "locations": _locations(posting),
            "description": description,
            "source": SITE,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType") or ("Remote" if posting.get("isRemote") else None),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update(_compensation(posting))
        yield _clean(fields)
