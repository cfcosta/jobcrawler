
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(
        r"(?i)<(?:br|/p|/li|/h[1-6]|/div|/section|/article|/tr|/ul|/ol)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = unescape(value)

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _location_name(location):
    option = location.get("location_option") or {}
    return (
        option.get("display_name")
        or location.get("name")
        or option.get("city")
    )


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            detail_url = f"https://app.dover.com/api/v1/inbound/application-portal-job/{job_id}"
            detail = json.loads(ctx.fetch(detail_url))

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            title = detail.get("title") or listing.get("title")
            description = _html_to_text(
                detail.get("user_provided_description") or ""
            )
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            for raw_location in raw_locations:
                name = _location_name(raw_location)
                if name:
                    locations.append(name)

            primary_location = None
            for raw_location in raw_locations:
                if raw_location.get("is_primary") is True:
                    primary_location = _location_name(raw_location)
                    if primary_location:
                        break
            if not primary_location and locations:
                primary_location = locations[0]
            if not primary_location:
                primary_location = detail.get("location")
            if not primary_location:
                continue

            kwargs = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": title,
                "company": "Activeloop",
                "location": primary_location,
                "description": description,
                "external_id": job_id,
            }

            if locations:
                kwargs["locations"] = locations

            workplace_type = (
                detail.get("workplace_type")
                or listing.get("workplace_type")
            )
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            group_name = group.get("name")
            if group_name and group_name != "Ungrouped":
                kwargs["department"] = group_name

            created = detail.get("created")
            if created:
                kwargs["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                if compensation.get("lower_bound") is not None:
                    kwargs["salary_min"] = compensation.get("lower_bound")
                if compensation.get("upper_bound") is not None:
                    kwargs["salary_max"] = compensation.get("upper_bound")
                if compensation.get("currency_code"):
                    kwargs["salary_currency"] = compensation.get("currency_code")
                if compensation.get("salary_range_type"):
                    kwargs["salary_period"] = compensation.get("salary_range_type")

            yield Job(**kwargs)
