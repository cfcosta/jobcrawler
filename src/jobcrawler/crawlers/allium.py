
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/allium?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        description = posting.get("descriptionPlain")
        title = posting.get("title")
        location = posting.get("location")
        url = posting.get("jobUrl")
        if not (title and location and url and description):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Allium",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }

        secondary = [
            item.get("location")
            for item in (posting.get("secondaryLocations") or [])
            if item.get("location")
        ]
        fields["locations"] = [location] + secondary

        workplace = posting.get("workplaceType")
        if workplace:
            fields["workplace_type"] = workplace
        elif posting.get("isRemote") is True:
            fields["workplace_type"] = "Remote"

        compensation = posting.get("compensation") or {}
        components = [
            item for item in (compensation.get("summaryComponents") or [])
            if item.get("compensationType") == "Salary"
        ]
        currencies = {item.get("currencyCode") for item in components}
        if len(components) == 1 and len(currencies) == 1 and None not in currencies:
            component = components[0]
            fields["salary_min"] = component.get("minValue")
            fields["salary_max"] = component.get("maxValue")
            fields["salary_currency"] = component.get("currencyCode")
            fields["salary_period"] = component.get("interval")
            printed = (
                compensation.get("compensationTierSummary")
                or compensation.get("scrapeableCompensationSalarySummary")
            )
            if printed:
                fields["salary"] = printed

        yield {key: value for key, value in fields.items() if value is not None}
