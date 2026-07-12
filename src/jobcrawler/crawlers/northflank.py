
import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/northflank.com?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Northflank",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            secondary_location = secondary.get("location")
            if secondary_location:
                locations.append(secondary_location)
        fields["locations"] = locations

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        compensation = posting.get("compensation") or {}
        salary_text = compensation.get("scrapeableCompensationSalarySummary")
        if salary_text:
            fields["salary"] = salary_text

        for component in compensation.get("summaryComponents") or []:
            if component.get("compensationType") != "Salary":
                continue
            minimum = component.get("minValue")
            maximum = component.get("maxValue")
            currency = component.get("currencyCode")
            period = component.get("interval")
            if minimum is not None:
                fields["salary_min"] = minimum
            if maximum is not None:
                fields["salary_max"] = maximum
            if currency:
                fields["salary_currency"] = currency
            if period:
                fields["salary_period"] = period
            break

        yield fields
