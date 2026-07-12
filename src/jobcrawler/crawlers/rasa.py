
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/Rasa?includeCompensation=true"
EXTRA_HOSTS = []


def _present(values):
    return [value for value in values if isinstance(value, str) and value.strip()]


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    salary = next(
        (
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ),
        None,
    )
    fields = {}

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if isinstance(printed, str) and printed.strip():
        fields["salary"] = printed

    if salary:
        if salary.get("minValue") is not None:
            fields["salary_min"] = salary["minValue"]
        if salary.get("maxValue") is not None:
            fields["salary_max"] = salary["maxValue"]
        if salary.get("currencyCode"):
            fields["salary_currency"] = salary["currencyCode"]
        if salary.get("interval"):
            fields["salary_period"] = salary["interval"]

    return fields


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        location = posting.get("location")
        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations") or []
            if isinstance(item, dict)
        ]
        locations = _present([location] + secondary)

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Rasa",
            "location": location,
            "description": posting.get("descriptionPlain"),
            "source": SITE,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        if locations:
            fields["locations"] = locations
        fields.update(_salary_fields(posting))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
