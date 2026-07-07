
import html
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/airwallex?includeCompensation=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _salary_from_compensation(comp):
    if not isinstance(comp, dict):
        return {}
    components = comp.get("summaryComponents")
    if not isinstance(components, list) or not components:
        return {}

    mins, maxes, currencies, intervals = [], [], [], []
    for component in components:
        if not isinstance(component, dict):
            continue
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        if isinstance(min_value, (int, float)):
            mins.append(min_value)
        if isinstance(max_value, (int, float)):
            maxes.append(max_value)
        if component.get("currencyCode"):
            currencies.append(component.get("currencyCode"))
        if component.get("interval"):
            intervals.append(component.get("interval"))

    salary = {}
    if mins:
        salary["salary_min"] = min(mins)
    if maxes:
        salary["salary_max"] = max(maxes)
    if currencies:
        salary["salary_currency"] = currencies[0]
    if intervals:
        salary["salary_interval"] = intervals[0]
    return salary


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        description = job.get("descriptionPlain") or _clean_html(job.get("descriptionHtml"))
        url = job.get("jobUrl") or ("https://jobs.ashbyhq.com/airwallex/" + job["id"])

        item = {
            "url": url,
            "title": job.get("title"),
            "company": "Airwallex",
            "location": job.get("location"),
            "description": description,
            "source": SITE,
            "external_id": job.get("id"),
            "department": job.get("department") or job.get("team"),
            "employment_type": job.get("employmentType"),
            "posted_at": job.get("publishedAt"),
            "apply_url": job.get("applyUrl"),
        }

        workplace_type = job.get("workplaceType")
        if workplace_type:
            item["workplace_type"] = workplace_type
        elif job.get("isRemote") is True:
            item["workplace_type"] = "Remote"

        item.update(_salary_from_compensation(job.get("compensation")))

        secondary = job.get("secondaryLocations")
        if secondary:
            names = []
            for loc in secondary:
                if isinstance(loc, dict) and loc.get("location"):
                    names.append(loc["location"])
                elif isinstance(loc, str):
                    names.append(loc)
            if names and item.get("location"):
                item["location"] = item["location"] + " / " + " / ".join(names)

        yield {k: v for k, v in item.items() if v not in (None, "")}
