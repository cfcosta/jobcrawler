import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/character?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        item = {
            "source": SITE,
            "url": posting.get("jobUrl"),
            "title": posting.get("title"),
            "company": "Character",
            "location": posting.get("location"),
            "description": posting.get("descriptionPlain"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
        }

        workplace_type = posting.get("workplaceType")
        if workplace_type:
            item["workplace_type"] = workplace_type
        elif posting.get("isRemote") is True:
            item["workplace_type"] = "Remote"

        secondary = posting.get("secondaryLocations") or []
        locations = []
        if posting.get("location"):
            locations.append(posting["location"])
        locations.extend(
            entry["location"]
            for entry in secondary
            if isinstance(entry, dict) and entry.get("location")
        )
        if secondary and locations:
            item["locations"] = locations

        compensation = posting.get("compensation") or {}
        if posting.get("shouldDisplayCompensationOnJobPostings"):
            printed_salary = compensation.get("compensationTierSummary")
            if printed_salary:
                item["salary"] = printed_salary

            salary_components = [
                component
                for component in compensation.get("summaryComponents", [])
                if component.get("compensationType") == "Salary"
            ]
            if len(salary_components) == 1:
                component = salary_components[0]
                if component.get("minValue") is not None:
                    item["salary_min"] = component["minValue"]
                if component.get("maxValue") is not None:
                    item["salary_max"] = component["maxValue"]
                if component.get("currencyCode"):
                    item["salary_currency"] = component["currencyCode"]
                if component.get("interval"):
                    item["salary_period"] = component["interval"]

        yield {
            key: value
            for key, value in item.items()
            if value is not None
        }
