import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/seon?includeCompensation=true"
EXTRA_HOSTS = []


def _response_text(response):
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return response.decode("utf-8")
    if hasattr(response, "text"):
        text = response.text
        return text() if callable(text) else text
    if hasattr(response, "content"):
        content = response.content
        return content.decode("utf-8") if isinstance(content, bytes) else str(content)
    return str(response)


def _locations(job):
    values = []
    primary = job.get("location")
    if primary:
        values.append(primary)

    for secondary in job.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            value = secondary.get("location")
        else:
            value = secondary
        if value and value not in values:
            values.append(value)

    return values


def _salary_fields(job):
    compensation = job.get("compensation") or {}
    if not job.get("shouldDisplayCompensationOnJobPostings"):
        return {}

    salary_components = [
        component
        for component in compensation.get("summaryComponents") or []
        if component.get("compensationType") == "Salary"
    ]
    if not salary_components:
        return {}

    # A single structured range can be represented by the Job salary quartet.
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
    if len(salary_components) != 1 or len(currencies) > 1 or len(intervals) > 1:
        return {}

    component = salary_components[0]
    result = {}
    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed
    if component.get("minValue") is not None:
        result["salary_min"] = component["minValue"]
    if component.get("maxValue") is not None:
        result["salary_max"] = component["maxValue"]
    if component.get("currencyCode"):
        result["salary_currency"] = component["currencyCode"]
    if component.get("interval"):
        result["salary_period"] = component["interval"]
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(_response_text(response))

    for posting in payload.get("jobs") or []:
        if posting.get("isListed") is False:
            continue

        title = posting.get("title")
        description = posting.get("descriptionPlain")
        location = posting.get("location")
        url = posting.get("jobUrl")
        if not title or not description or not location or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "SEON",
            "location": location,
            "description": description,
            "source": "Ashby",
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department"),
            "posted_at": posting.get("publishedAt"),
        }

        locations = _locations(posting)
        if locations:
            fields["locations"] = locations

        fields.update(_salary_fields(posting))
        yield {key: value for key, value in fields.items() if value is not None}


