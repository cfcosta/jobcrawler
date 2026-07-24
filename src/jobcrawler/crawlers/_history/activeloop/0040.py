import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
        " ",
        value,
    )
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)<\s*(?:p|div|h[1-6]|section|article)\b[^>]*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines) or None


def _location_name(location):
    option = location.get("location_option") or {}
    return option.get("display_name") or location.get("name")


def _description(record):
    for key in ("user_provided_description", "user_facing_description"):
        text = _html_to_text(record.get(key))
        if text:
            return text

    generated = record.get("generated_description") or {}
    sections = []
    for key in (
        "about_the_role",
        "job_mandates",
        "qualifications",
        "about_the_company",
        "additional_information",
    ):
        text = _html_to_text(generated.get(key))
        if text:
            sections.append(text)
    return "\n\n".join(sections) or None


def crawl(ctx):
    board_response = ctx.fetch(SITE)
    groups = json.loads(board_response)

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            external_id = listing.get("id")
            if not external_id:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{external_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + external_id
            )
            detail_response = ctx.fetch(detail_url)
            detail = json.loads(detail_response)

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _description(detail)
            if not description:
                description_url = (
                    f"https://app.dover.com/api/v1/jobs/{external_id}/"
                    "get_job_description"
                )
                description_response = ctx.fetch(description_url)
                description = _description(json.loads(description_response))
            if not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            for item in raw_locations:
                name = _location_name(item)
                if name:
                    locations.append(name)

            primary = None
            for item in raw_locations:
                if item.get("is_primary"):
                    primary = _location_name(item)
                    if primary:
                        break

            location = (
                detail.get("location")
                or primary
                or (locations[0] if locations else None)
            )
            if not location:
                continue

            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": external_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }

            if locations:
                fields["locations"] = locations

            group_name = group.get("name")
            if group_name and group_name.lower() != "ungrouped":
                fields["department"] = group_name

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]

            if compensation.get("open_to_sharing_comp") is True:
                if compensation.get("lower_bound") is not None:
                    fields["salary_min"] = compensation["lower_bound"]
                if compensation.get("upper_bound") is not None:
                    fields["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            if detail.get("compensation_details"):
                fields["salary"] = detail["compensation_details"]

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )
