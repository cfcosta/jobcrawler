from html import unescape
import json
from jobcrawler.runtime.contract import Job
import re


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|blockquote|pre|table|tr|td|th)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _display_location(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        department = group.get("name")
        if not department or department == "Ungrouped":
            department = None

        for summary in group.get("jobs") or []:
            if summary.get("is_published") is not True:
                continue
            if summary.get("is_sample") is True:
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            detail_url = (
                f"https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            description_url = (
                f"https://app.dover.com/api/v1/jobs/{job_id}/"
                f"get_job_description"
            )

            detail = json.loads(ctx.fetch(detail_url))
            description_data = json.loads(ctx.fetch(description_url))
            description_html = (
                description_data.get("user_provided_description")
                or detail.get("user_provided_description")
                or description_data.get("user_facing_description")
            )
            description = _html_to_text(description_html)
            if not description:
                continue

            location_items = (
                detail.get("locations")
                or summary.get("locations")
                or []
            )
            locations = []
            primary = None
            for item in location_items:
                display = _display_location(item)
                if display:
                    locations.append(display)
                    if item.get("is_primary") and primary is None:
                        primary = display

            location = primary or (locations[0] if locations else None)
            if not location:
                continue

            kwargs = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": detail.get("client_name") or "Activeloop",
                "location": location,
                "locations": locations,
                "description": description,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or summary.get("workplace_type")
                ),
                "department": department,
            }

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if lower is not None:
                    kwargs["salary_min"] = lower
                if upper is not None:
                    kwargs["salary_max"] = upper
                if currency:
                    kwargs["salary_currency"] = currency
                if period:
                    kwargs["salary_period"] = period

            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]

            yield Job(**kwargs)