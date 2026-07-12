
import html
import json
import re


SITE = "https://api.ashbyhq.com/posting-api/job-board/unit?includeCompensation=true"
EXTRA_HOSTS = []


def _plain_description(posting):
    plain = posting.get("descriptionPlain")
    if plain and plain.strip():
        return plain.strip()

    markup = posting.get("descriptionHtml")
    if not markup:
        return None

    text = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    text = re.sub(
        r"(?i)</?(?:p|div|li|h[1-6]|ul|ol|section|article)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _secondary_location(value):
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        location = value.get("location")
        if isinstance(location, str) and location:
            return location
    return None


def _salary_fields(posting):
    if posting.get("shouldDisplayCompensationOnJobPostings") is not True:
        return {}

    compensation = posting.get("compensation") or {}
    components = compensation.get("summaryComponents") or []
    salary_components = [
        component
        for component in components
        if component.get("compensationType") == "Salary"
    ]
    if len(salary_components) != 1:
        return {}

    component = salary_components[0]
    result = {}
    field_map = {
        "minValue": "salary_min",
        "maxValue": "salary_max",
        "currencyCode": "salary_currency",
        "interval": "salary_period",
    }
    for source_name, target_name in field_map.items():
        value = component.get(source_name)
        if value is not None and value != "":
            result[target_name] = value

    printed = compensation.get("scrapeableCompensationSalarySummary")
    if printed:
        result["salary"] = printed
    return result


def crawl(ctx):
    response = ctx.fetch(SITE)
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        if posting.get("isListed") is not True:
            continue

        title = posting.get("title")
        location = posting.get("location")
        description = _plain_description(posting)
        url = posting.get("jobUrl")
        if not title or not location or not description or not url:
            continue

        locations = [location]
        for secondary in posting.get("secondaryLocations") or []:
            value = _secondary_location(secondary)
            if value and value not in locations:
                locations.append(value)

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": "Unit",
            "location": location,
            "locations": locations,
            "description": description,
            "external_id": posting.get("id"),
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": posting.get("department") or posting.get("team"),
            "posted_at": posting.get("publishedAt"),
            "apply_url": posting.get("applyUrl"),
        }
        fields.update(_salary_fields(posting))
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield fields
