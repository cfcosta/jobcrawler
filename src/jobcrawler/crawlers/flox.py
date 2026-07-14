import json


SITE = 'https://api.ashbyhq.com/posting-api/job-board/flox?includeCompensation=true'
EXTRA_HOSTS = []


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "json"):
        data = response.json()
    elif hasattr(response, "text"):
        data = response.text
    else:
        data = response

    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    if isinstance(data, str):
        data = json.loads(data)

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        fields = {
            "url": item["jobUrl"],
            "title": item["title"],
            "company": "Flox",
            "location": item["location"],
            "description": item["descriptionPlain"],
            "source": SITE,
        }

        locations = []
        if item.get("location"):
            locations.append(item["location"])
        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location")
            if value and value not in locations:
                locations.append(value)
        if locations:
            fields["locations"] = locations

        optional_mapping = {
            "employmentType": "employment_type",
            "workplaceType": "workplace_type",
            "department": "department",
            "publishedAt": "posted_at",
            "id": "external_id",
            "applyUrl": "apply_url",
        }
        for source_key, job_key in optional_mapping.items():
            value = item.get(source_key)
            if value is not None and value != "":
                fields[job_key] = value

        compensation = item.get("compensation") or {}
        salary_text = compensation.get("compensationTierSummary")
        if salary_text:
            fields["salary"] = salary_text

        salary_components = [
            component
            for component in compensation.get("summaryComponents") or []
            if component.get("compensationType") == "Salary"
        ]
        if len(salary_components) == 1:
            component = salary_components[0]
            salary_mapping = {
                "minValue": "salary_min",
                "maxValue": "salary_max",
                "currencyCode": "salary_currency",
                "interval": "salary_period",
            }
            for source_key, job_key in salary_mapping.items():
                value = component.get(source_key)
                if value is not None and value != "":
                    fields[job_key] = value

        yield fields
