
import json


SITE = "https://api.ashbyhq.com/posting-api/job-board/backmarket?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if isinstance(response, str):
        data = json.loads(response)
    elif isinstance(response, bytes):
        data = json.loads(response.decode("utf-8"))
    else:
        try:
            data = response.json()
        except AttributeError:
            data = json.loads(response.text)

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = posting.get("descriptionPlain")
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Back Market",
            "location": location,
            "description": description,
            "source": SITE,
        }

        locations = [location]
        locations.extend(
            item["location"]
            for item in posting.get("secondaryLocations") or []
            if isinstance(item, dict) and item.get("location")
        )
        fields["locations"] = locations

        optional = {
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }
        fields.update({key: value for key, value in optional.items() if value})

        compensation = posting.get("compensation") or {}
        summary = compensation.get("compensationTierSummary")
        if summary:
            fields["salary"] = summary

        salary_component = next(
            (
                component
                for component in compensation.get("summaryComponents") or []
                if isinstance(component, dict)
                and component.get("compensationType") == "Salary"
            ),
            None,
        )
        if salary_component:
            salary_fields = {
                "salary_min": salary_component.get("minValue"),
                "salary_max": salary_component.get("maxValue"),
                "salary_currency": salary_component.get("currencyCode"),
                "salary_period": salary_component.get("interval"),
            }
            fields.update({
                key: value
                for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        yield fields
