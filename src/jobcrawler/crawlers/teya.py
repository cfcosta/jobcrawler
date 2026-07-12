
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/teya?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)

    seen = set()
    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        external_id = posting.get("id")
        url = posting.get("jobUrl")
        dedupe_key = external_id or url
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        if not description:
            continue

        fields = {
            "source": SITE,
            "url": url,
            "title": title,
            "company": "Teya",
            "location": location,
            "description": description,
            "external_id": external_id,
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations", [])
            if item.get("location")
        ]
        if secondary:
            fields["locations"] = [location] + secondary

        if posting.get("shouldDisplayCompensationOnJobPostings"):
            compensation = posting.get("compensation") or {}
            components = compensation.get("summaryComponents") or []
            salary_components = [
                component
                for component in components
                if component.get("compensationType") == "Salary"
            ]
            currencies = {
                component.get("currencyCode")
                for component in salary_components
                if component.get("currencyCode")
            }
            intervals = {
                component.get("interval")
                for component in salary_components
                if component.get("interval")
            }

            if len(salary_components) == 1 and len(currencies) <= 1 and len(intervals) <= 1:
                component = salary_components[0]
                fields["salary_min"] = component.get("minValue")
                fields["salary_max"] = component.get("maxValue")
                fields["salary_currency"] = component.get("currencyCode")
                fields["salary_period"] = component.get("interval")
                fields["salary"] = compensation.get("compensationTierSummary")

        yield {key: value for key, value in fields.items() if value is not None}
