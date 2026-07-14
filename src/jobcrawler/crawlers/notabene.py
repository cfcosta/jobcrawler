
import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/notabene?includeCompensation=true"
EXTRA_HOSTS = []


def _put(values, key, value):
    if value is not None and value != "":
        values[key] = value


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        if not posting.get("isListed", True):
            continue

        location = posting.get("location")
        values = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Notabene",
            "location": location,
            "description": (
                posting.get("descriptionPlain")
                or posting.get("descriptionHtml")
            ),
            "source": SITE,
        }

        secondary = posting.get("secondaryLocations")
        if secondary is not None:
            locations = []
            if location:
                locations.append(location)
            for item in secondary:
                value = item.get("location") if isinstance(item, dict) else item
                if value:
                    locations.append(value)
            if locations:
                values["locations"] = locations

        _put(values, "employment_type", posting.get("employmentType"))
        _put(values, "workplace_type", posting.get("workplaceType"))
        _put(values, "department", posting.get("department"))
        _put(values, "posted_at", posting.get("publishedAt"))
        _put(values, "external_id", posting.get("id"))
        _put(values, "apply_url", posting.get("applyUrl"))

        compensation = posting.get("compensation") or {}
        printed = (
            compensation.get("compensationTierSummary")
            or compensation.get("scrapeableCompensationSalarySummary")
        )
        _put(values, "salary", printed)

        components = compensation.get("summaryComponents") or []
        salary_components = [
            component for component in components
            if component.get("minValue") is not None
            or component.get("maxValue") is not None
        ]
        currencies = {
            component.get("currencyCode")
            for component in salary_components
            if component.get("currencyCode")
        }
        if salary_components and len(currencies) <= 1:
            component = salary_components[0]
            _put(values, "salary_min", component.get("minValue"))
            _put(values, "salary_max", component.get("maxValue"))
            _put(values, "salary_currency", component.get("currencyCode"))
            _put(values, "salary_period", component.get("interval"))

        yield values
