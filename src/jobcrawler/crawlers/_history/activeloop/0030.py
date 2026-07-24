
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/section|/article|/h[1-6]|/li|/ul|/ol|/blockquote|/pre|/tr)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listed in group.get("jobs", []):
            if listed.get("is_published") is not True:
                continue
            if listed.get("is_sample") is not False:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = []
            primary_location = None
            for entry in raw_locations:
                option = entry.get("location_option") or {}
                name = option.get("display_name") or entry.get("name")
                if not name:
                    continue
                locations.append(name)
                if entry.get("is_primary") is True and primary_location is None:
                    primary_location = name

            display_location = detail.get("location") or primary_location
            if not display_location and locations:
                display_location = locations[0]
            if not display_location:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": detail.get("client_name") or "Activeloop",
                "location": display_location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type") or listed.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }

            if locations:
                fields["locations"] = locations

            group_name = group.get("name")
            if group_name and group_name.lower() != "ungrouped":
                fields["department"] = group_name

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                if compensation.get("lower_bound") is not None:
                    fields["salary_min"] = compensation["lower_bound"]
                if compensation.get("upper_bound") is not None:
                    fields["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )
