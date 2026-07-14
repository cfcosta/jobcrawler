import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/cointracker?includeCompensation=true"
EXTRA_HOSTS = []


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue

        fields = {"company": "CoinTracker", "source": SITE}

        mappings = {
            "title": "title",
            "url": "jobUrl",
            "apply_url": "applyUrl",
            "description": "descriptionPlain",
            "location": "location",
            "external_id": "id",
            "employment_type": "employmentType",
            "workplace_type": "workplaceType",
            "department": "department",
            "posted_at": "publishedAt",
        }

        for target, source in mappings.items():
            value = item.get(source)
            if value != "":
                if value is not None:
                    fields[target] = value

        locations = []
        primary = item.get("location")
        if primary:
            locations.append(primary)

        for secondary in item.get("secondaryLocations") or []:
            value = secondary.get("location") if isinstance(secondary, dict) else None
            if value and value not in locations:
                locations.append(value)

        if locations:
            fields["locations"] = locations

        compensation = item.get("compensation") or {}
        summary = compensation.get("summary")
        if isinstance(summary, str) and summary:
            fields["salary"] = summary

        for component in compensation.get("summaryComponents") or []:
            if not isinstance(component, dict):
                continue

            minimum = component.get("minValue")
            maximum = component.get("maxValue")
            currency = component.get("currencyCode")
            interval = component.get("interval")

            if (
                isinstance(minimum, (int, float))
                and not isinstance(minimum, bool)
                and isinstance(maximum, (int, float))
                and not isinstance(maximum, bool)
                and isinstance(currency, str)
                and currency
                and isinstance(interval, str)
                and interval
            ):
                fields.update(
                    {
                        "salary_min": minimum,
                        "salary_max": maximum,
                        "salary_currency": currency,
                        "salary_period": interval,
                    }
                )
                break

        yield fields