
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/helpscout?includeCompensation=true"
EXTRA_HOSTS = []


def _set_if_present(values, key, value):
    if value is not None and value != "":
        values[key] = value


def _salary_component(job):
    compensation = job.get("compensation") or {}
    for component in compensation.get("summaryComponents") or []:
        if component.get("compensationType") == "Salary":
            return component
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        locations = []
        if location:
            locations.append(location)
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location and secondary_location not in locations:
                locations.append(secondary_location)

        values = {
            "url": posting.get("jobUrl") or (
                "https://jobs.ashbyhq.com/helpscout/" + posting["id"]
            ),
            "title": posting.get("title"),
            "company": "Help Scout",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
        }

        if locations:
            values["locations"] = locations

        _set_if_present(values, "external_id", posting.get("id"))
        _set_if_present(values, "apply_url", posting.get("applyUrl"))
        _set_if_present(
            values,
            "department",
            posting.get("department") or posting.get("team"),
        )
        _set_if_present(values, "employment_type", posting.get("employmentType"))
        _set_if_present(
            values,
            "workplace_type",
            posting.get("workplaceType"),
        )
        _set_if_present(values, "posted_at", posting.get("publishedAt"))

        compensation = posting.get("compensation") or {}
        salary_component = _salary_component(posting)
        if salary_component:
            _set_if_present(
                values,
                "salary",
                compensation.get("scrapeableCompensationSalarySummary")
                or salary_component.get("summary"),
            )
            _set_if_present(
                values, "salary_min", salary_component.get("minValue")
            )
            _set_if_present(
                values, "salary_max", salary_component.get("maxValue")
            )
            _set_if_present(
                values,
                "salary_currency",
                salary_component.get("currencyCode"),
            )
            _set_if_present(
                values,
                "salary_period",
                salary_component.get("interval"),
            )

        yield values
