import json

SITE = 'https://api.ashbyhq.com/posting-api/job-board/Encord?includeCompensation=true'
EXTRA_HOSTS = []


def _json_response(response):
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "read"):
        response = response.read()
    elif hasattr(response, "text"):
        response = response.text
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    return json.loads(response)


def crawl(ctx):
    data = _json_response(ctx.fetch(SITE))

    for posting in data.get("jobs", []):
        if posting.get("isListed") is False:
            continue

        fields = {
            "url": posting["jobUrl"],
            "title": posting["title"],
            "company": "Encord",
            "location": posting["location"],
            "description": posting["descriptionPlain"],
            "source": SITE,
        }

        optional = {
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update({key: value for key, value in optional.items() if value is not None and value != ""})

        secondary = [
            item.get("location")
            for item in posting.get("secondaryLocations", [])
            if item.get("location")
        ]
        if secondary:
            fields["locations"] = [posting["location"]] + secondary

        components = (posting.get("compensation") or {}).get("summaryComponents") or []
        salaries = [
            component for component in components
            if component.get("compensationType") == "Salary"
        ]
        if len(salaries) == 1:
            salary_component = salaries[0]
            salary_text = (posting.get("compensation") or {}).get("scrapeableCompensationSalarySummary")
            salary_fields = {
                "salary": salary_text,
                "salary_min": salary_component.get("minValue"),
                "salary_max": salary_component.get("maxValue"),
                "salary_currency": salary_component.get("currencyCode"),
                "salary_period": salary_component.get("interval"),
            }
            fields.update({
                key: value for key, value in salary_fields.items()
                if value is not None and value != ""
            })

        yield fields
