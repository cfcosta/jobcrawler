
import json
import re

SITE = "https://jobs.ashbyhq.com/uniswap"
EXTRA_HOSTS = []


def _app_data(page):
    if not isinstance(page, str):
        page = page.text
    match = re.search(
        r"window\.__appData\s*=\s*(\{.*?\});\s*(?:\n|fetch\()",
        page,
        re.S,
    )
    if not match:
        raise ValueError("Ashby app data was not found")
    return json.loads(match.group(1))


def _locations(posting):
    values = []
    primary = posting.get("locationExternalName") or posting.get("locationName")
    if primary:
        values.append(primary)

    secondary = posting.get("secondaryLocationNames") or []
    for item in secondary:
        if isinstance(item, dict):
            value = (
                item.get("locationExternalName")
                or item.get("locationName")
                or item.get("location")
            )
        else:
            value = item
        if value and value not in values:
            values.append(value)
    return values


def _salary_fields(posting):
    result = {}
    if posting.get("shouldDisplayCompensation"):
        printed = (
            posting.get("compensationTierSummary")
            or posting.get("scrapeableCompensationSalarySummary")
        )
        if printed:
            result["salary"] = printed

        for component in posting.get("summaryComponents") or []:
            if not isinstance(component, dict):
                continue
            mapping = {
                "salary_min": component.get("minValue"),
                "salary_max": component.get("maxValue"),
                "salary_currency": component.get("currencyCode"),
                "salary_period": component.get("interval"),
            }
            if any(value is not None for value in mapping.values()):
                result.update(
                    (key, value) for key, value in mapping.items()
                    if value is not None
                )
                break
    return result


def crawl(ctx):
    board_data = _app_data(ctx.fetch(SITE))
    board = board_data.get("jobBoard") or {}

    seen = set()
    for summary in board.get("jobPostings") or []:
        posting_id = summary.get("id")
        if not posting_id or posting_id in seen or summary.get("isListed") is False:
            continue
        seen.add(posting_id)

        url = SITE.rstrip("/") + "/" + posting_id
        detail_data = _app_data(ctx.fetch(url))
        posting = detail_data.get("posting") or {}
        if posting.get("isListed") is False:
            continue

        locations = _locations(posting)
        location = (
            posting.get("locationExternalName")
            or posting.get("locationName")
            or (locations[0] if locations else None)
        )
        description = posting.get("descriptionPlainText")

        job = {
            "source": SITE,
            "url": url,
            "title": posting.get("title") or summary.get("title"),
            "company": "Uniswap Labs",
            "location": location,
            "description": description,
            "external_id": posting.get("id") or posting_id,
            "employment_type": posting.get("employmentType"),
            "workplace_type": posting.get("workplaceType"),
            "department": (
                posting.get("departmentExternalName")
                or posting.get("departmentName")
                or posting.get("teamExternalName")
                or posting.get("teamName")
            ),
            "posted_at": posting.get("publishedDate"),
            "expires_at": posting.get("applicationDeadline"),
        }
        if locations:
            job["locations"] = locations
        job.update(_salary_fields(posting))

        yield {
            key: value for key, value in job.items()
            if value is not None and value != ""
        }
