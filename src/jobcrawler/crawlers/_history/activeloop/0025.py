
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _as_json(response):
    if isinstance(response, str):
        return json.loads(response)
    return response.json()


def _description_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</\s*(?:p|div|h[1-6]|li|ul|ol|section|article|tr)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)\s*>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location_name(location):
    option = location.get("location_option") or {}
    return (
        option.get("display_name")
        or location.get("name")
        or option.get("city")
    )


def crawl(ctx):
    groups = _as_json(ctx.fetch(SITE + "?format=json"))

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
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = _as_json(ctx.fetch(detail_url))

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _description_text(
                detail.get("user_provided_description")
            )
            if not description:
                continue

            raw_locations = (
                detail.get("locations")
                or listing.get("locations")
                or []
            )
            locations = []
            primary_location = None
            for raw_location in raw_locations:
                name = _location_name(raw_location)
                if not name:
                    continue
                locations.append(name)
                if raw_location.get("is_primary"):
                    primary_location = name

            location = primary_location or detail.get("location")
            if not location and locations:
                location = locations[0]

            compensation = detail.get("compensation") or {}
            fields = {
                "url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": apply_url,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
            }

            if locations:
                fields["locations"] = locations

            if compensation.get("open_to_sharing_comp") is True:
                fields["salary_min"] = compensation.get("lower_bound")
                fields["salary_max"] = compensation.get("upper_bound")
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None and value != ""
            })
