
import json

SITE = "https://jobs.ashbyhq.com/chainlink-labs"
EXTRA_HOSTS = []

_MARKER = "window.__appData = "


def _app_data(html):
    start = html.find(_MARKER)
    if start < 0:
        raise ValueError("Ashby app data was not found")
    start += len(_MARKER)
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    return data


def _value(data, key):
    value = data.get(key)
    return value if value not in (None, "") else None


def crawl(ctx):
    listing = _app_data(ctx.fetch(SITE))
    postings = listing.get("jobBoard", {}).get("jobPostings", [])

    seen = set()
    for summary in postings:
        posting_id = summary.get("id")
        if not posting_id or posting_id in seen or summary.get("isListed") is False:
            continue
        seen.add(posting_id)

        url = SITE + "/" + posting_id
        detail = _app_data(ctx.fetch(url)).get("posting") or {}
        if detail.get("isListed") is False:
            continue

        description = _value(detail, "descriptionPlainText")
        title = _value(detail, "title") or _value(summary, "title")
        location = _value(detail, "locationName") or _value(summary, "locationName")
        if not description or not title or not location:
            continue

        secondary = detail.get("secondaryLocationNames")
        if not isinstance(secondary, list):
            secondary = [
                item.get("locationName")
                for item in summary.get("secondaryLocations", [])
                if isinstance(item, dict) and item.get("locationName")
            ]
        locations = [location]
        locations.extend(
            item for item in secondary
            if isinstance(item, str) and item and item not in locations
        )

        fields = {
            "url": url,
            "source": SITE,
            "title": title.strip(),
            "company": "Chainlink Labs",
            "location": location,
            "locations": locations,
            "description": description,
            "external_id": _value(detail, "jobId") or posting_id,
            "apply_url": url + "/application",
            "department": (
                _value(detail, "teamName")
                or _value(detail, "departmentName")
                or _value(summary, "teamName")
                or _value(summary, "departmentName")
            ),
            "employment_type": (
                _value(detail, "employmentType")
                or _value(summary, "employmentType")
            ),
            "workplace_type": (
                _value(detail, "workplaceType")
                or _value(summary, "workplaceType")
            ),
            "posted_at": (
                _value(detail, "publishedDate")
                or _value(summary, "publishedDate")
            ),
            "expires_at": (
                _value(detail, "applicationDeadline")
                or _value(summary, "applicationDeadline")
            ),
        }

        salary = (
            _value(detail, "scrapeableCompensationSalarySummary")
            or _value(detail, "compensationTierSummary")
        )
        if salary:
            fields["salary"] = salary

        salary_components = [
            component
            for component in (detail.get("summaryComponents") or [])
            if isinstance(component, dict)
            and component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            if component.get("minValue") is not None:
                fields["salary_min"] = component["minValue"]
            if component.get("maxValue") is not None:
                fields["salary_max"] = component["maxValue"]
            if component.get("currencyCode"):
                fields["salary_currency"] = component["currencyCode"]
            if component.get("interval"):
                fields["salary_period"] = component["interval"]

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
