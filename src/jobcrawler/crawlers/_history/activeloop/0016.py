
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _clean_html(value):
    value = value or ""
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)<(?:br|p|div|li|ul|ol|h[1-6]|section|article|tr|table|blockquote|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)</(?:p|div|li|ul|ol|h[1-6]|section|article|tr|table|blockquote)>", "\n", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True:
                continue
            if listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            portal_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            portal = json.loads(ctx.fetch(portal_url))
            if portal.get("active") is not True:
                continue
            if portal.get("is_private") is True:
                continue

            description_html = portal.get("user_provided_description")
            description_url = (
                "https://app.dover.com/api/v1/jobs/"
                + job_id
                + "/get_job_description"
            )
            detail = json.loads(ctx.fetch(description_url))
            if detail.get("is_published") is True:
                description_html = (
                    detail.get("user_provided_description")
                    or detail.get("user_facing_description")
                    or description_html
                )

            description = _clean_html(description_html)
            if not description:
                continue

            raw_locations = portal.get("locations") or listed.get("locations") or []
            locations = [
                name for name in map(_location_name, raw_locations) if name
            ]
            primary = next(
                (
                    _location_name(item)
                    for item in raw_locations
                    if item.get("is_primary") and _location_name(item)
                ),
                None,
            )
            location = (
                portal.get("location")
                or primary
                or (locations[0] if locations else None)
            )
            if not location:
                continue

            apply_url = (
                "https://app.dover.com/apply/Activeloop/" + job_id + "/"
            )
            values = {
                "url": apply_url,
                "title": portal.get("title") or listed.get("title"),
                "company": portal.get("client_name") or "Activeloop",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": (
                    portal.get("workplace_type")
                    or listed.get("workplace_type")
                ),
                "posted_at": portal.get("created"),
            }

            if locations:
                values["locations"] = locations

            department = group.get("name")
            if department and department != "Ungrouped":
                values["department"] = department

            compensation = portal.get("compensation") or {}
            if compensation.get("open_to_sharing_comp") is True:
                values["salary_min"] = compensation.get("lower_bound")
                values["salary_max"] = compensation.get("upper_bound")
                values["salary_currency"] = compensation.get("currency_code")
                values["salary_period"] = compensation.get("salary_range_type")

            if compensation.get("employment_type"):
                values["employment_type"] = compensation["employment_type"]

            yield Job(
                **{
                    key: value
                    for key, value in values.items()
                    if value is not None and value != ""
                }
            )
