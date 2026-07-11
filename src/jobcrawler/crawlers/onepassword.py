
import json
import re
import html

SITE = 'https://api.ashbyhq.com/posting-api/job-board/1password?includeCompensation=true'
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip() or None


def _location_name(value):
    if isinstance(value, dict):
        return value.get("location") or value.get("name")
    if isinstance(value, str):
        return value
    return None


def _add(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        title = item.get("title")
        url = item.get("jobUrl")
        location = item.get("location")
        description = item.get("descriptionPlain") or _clean_html(item.get("descriptionHtml"))
        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "1Password",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = []
        primary = _location_name(location)
        if primary:
            locations.append(primary)
        for secondary in item.get("secondaryLocations") or []:
            name = _location_name(secondary)
            if name and name not in locations:
                locations.append(name)
        if locations:
            job["locations"] = locations

        _add(job, "employment_type", item.get("employmentType"))
        _add(job, "workplace_type", item.get("workplaceType"))
        _add(job, "department", item.get("department") or item.get("team"))
        _add(job, "posted_at", item.get("publishedAt"))
        _add(job, "external_id", item.get("id"))
        _add(job, "apply_url", item.get("applyUrl"))

        comp = item.get("compensation") or {}
        salary_text = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
        _add(job, "salary", salary_text)

        components = comp.get("summaryComponents") or []
        currencies = {c.get("currencyCode") for c in components if c.get("currencyCode")}
        if len(currencies) == 1:
            mins = [c.get("minValue") for c in components if c.get("minValue") is not None]
            maxs = [c.get("maxValue") for c in components if c.get("maxValue") is not None]
            periods = [c.get("interval") for c in components if c.get("interval")]
            if mins:
                job["salary_min"] = min(mins)
            if maxs:
                job["salary_max"] = max(maxs)
            job["salary_currency"] = next(iter(currencies))
            if periods:
                job["salary_period"] = periods[0]

        yield job
