import html
import json
import re

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|blockquote|pre|tr|table)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        department = group.get("name")
        if department == "Ungrouped":
            department = None

        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True:
                continue
            if listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is not True:
                continue
            if detail.get("is_private") is True:
                continue

            description = _text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = _location_name(item)
                if not name:
                    continue
                locations.append(name)
                if item.get("is_primary"):
                    primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            job = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": title,
                "company": "Activeloop",
                "location": location,
                "locations": locations or None,
                "description": description,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listed.get("workplace_type")
                ),
                "department": department,
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            if compensation.get("open_to_sharing_comp") is True:
                job["salary_min"] = compensation.get("lower_bound")
                job["salary_max"] = compensation.get("upper_bound")
                job["salary_currency"] = compensation.get("currency_code")
                job["salary_period"] = compensation.get("salary_range_type")

            employment_type = compensation.get("employment_type")
            if employment_type:
                job["employment_type"] = employment_type

            yield {
                key: value
                for key, value in job.items()
                if value is not None
            }
