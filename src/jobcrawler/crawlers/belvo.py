import html
import json
import re

SITE = 'https://api.ashbyhq.com/posting-api/job-board/belvo?includeCompensation=true'
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def _put(fields, key, value):
    if value is not None and value != "":
        fields[key] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        description = item.get("descriptionPlain") or _html_to_text(
            item.get("descriptionHtml")
        )
        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Belvo",
            "location": item.get("location"),
            "description": description,
            "source": SITE,
        }

        _put(fields, "external_id", item.get("id"))
        _put(fields, "apply_url", item.get("applyUrl"))
        _put(fields, "department", item.get("department") or item.get("team"))
        _put(fields, "employment_type", item.get("employmentType"))
        _put(fields, "posted_at", item.get("publishedAt"))

        workplace = item.get("workplaceType")
        if not workplace and item.get("isRemote") is True:
            workplace = "Remote"
        _put(fields, "workplace_type", workplace)

        secondary = item.get("secondaryLocations") or []
        if secondary:
            locations = []
            if item.get("location"):
                locations.append(item["location"])
            for entry in secondary:
                value = entry.get("location") if isinstance(entry, dict) else entry
                if value:
                    locations.append(value)
            if locations:
                fields["locations"] = locations

        compensation = item.get("compensation") or {}
        printed_salary = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        _put(fields, "salary", printed_salary)

        components = compensation.get("summaryComponents") or []
        salary_components = [
            component for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        if salary_components:
            currencies = {
                component.get("currencyCode")
                for component in salary_components
                if component.get("currencyCode")
            }
            if len(currencies) <= 1:
                component = salary_components[0]
                _put(fields, "salary_min", component.get("minValue"))
                _put(fields, "salary_max", component.get("maxValue"))
                _put(fields, "salary_currency", component.get("currencyCode"))
                _put(fields, "salary_period", component.get("interval"))

        yield fields
