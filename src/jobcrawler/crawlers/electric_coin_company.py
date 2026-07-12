import json
import re

SITE = 'https://api.ashbyhq.com/posting-api/job-board/zodl?includeCompensation=true'
EXTRA_HOSTS = []


def _salary_fields(description):
    if not description:
        return {}

    match = re.search(
        r"(?im)^Compensation:\s*(\$\s*([0-9]+(?:\.[0-9]+)?)\s*[Kk]"
        r"\s*[-\u2013\u2014]\s*\$?\s*([0-9]+(?:\.[0-9]+)?)\s*[Kk]"
        r"[^\r\n]*)",
        description,
    )
    if not match:
        return {}

    return {
        "salary": match.group(1).strip(),
        "salary_min": float(match.group(2)) * 1000,
        "salary_max": float(match.group(3)) * 1000,
        "salary_currency": "USD",
        "salary_period": "YEAR",
    }


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        location = item.get("location")
        locations = []
        if location:
            locations.append(location)
        for entry in item.get("secondaryLocations") or []:
            value = entry.get("location") if isinstance(entry, dict) else None
            if value and value not in locations:
                locations.append(value)

        description = item.get("descriptionPlain")
        fields = {
            "url": item.get("jobUrl"),
            "title": item.get("title"),
            "company": "Electric Coin Company",
            "location": location,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "employment_type": item.get("employmentType"),
            "workplace_type": item.get("workplaceType"),
            "department": item.get("department"),
            "posted_at": item.get("publishedAt"),
            "external_id": item.get("id"),
            "apply_url": item.get("applyUrl"),
        }
        fields.update(_salary_fields(description))
        yield {key: value for key, value in fields.items()
               if value is not None and value != ""}
