
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
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")

    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines()
    ]
    result = "\n".join(line for line in lines if line)
    return result or None


def _location_name(item):
    if not isinstance(item, dict):
        return None

    option = item.get("location_option")
    if isinstance(option, dict) and option.get("display_name"):
        return option["display_name"]

    return item.get("name") or None


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))
    if not isinstance(groups, list):
        return

    for group in groups:
        if not isinstance(group, dict):
            continue

        jobs = group.get("jobs") or []
        if not isinstance(jobs, list):
            continue

        for listing in jobs:
            if not isinstance(listing, dict):
                continue
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                "application-portal-job/" + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not isinstance(detail, dict):
                continue
            if detail.get("active") is not True:
                continue

            apply_url = (
                "https://app.dover.com/apply/Activeloop/"
                + job_id
                + "/"
            )

            raw_locations = (
                detail.get("locations")
                or listing.get("locations")
                or []
            )
            locations = [
                name
                for name in map(_location_name, raw_locations)
                if name
            ]
            primary_location = next(
                (
                    _location_name(item)
                    for item in raw_locations
                    if isinstance(item, dict) and item.get("is_primary")
                ),
                None,
            )
            location = (
                primary_location
                or (locations[0] if locations else None)
                or detail.get("location")
            )

            compensation = detail.get("compensation")
            salary_min = None
            salary_max = None
            salary_currency = None
            salary_period = None
            employment_type = None

            if isinstance(compensation, dict):
                employment_type = compensation.get("employment_type")
                if compensation.get("open_to_sharing_comp") is True:
                    salary_min = compensation.get("lower_bound")
                    salary_max = compensation.get("upper_bound")
                    salary_currency = compensation.get("currency_code")
                    salary_period = compensation.get("salary_range_type")

            yield Job(
                source=SITE,
                url=apply_url,
                apply_url=apply_url,
                title=detail.get("title") or listing.get("title"),
                company="Activeloop",
                location=location,
                locations=locations or None,
                description=_html_to_text(
                    detail.get("user_provided_description")
                ),
                workplace_type=(
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                employment_type=employment_type,
                salary_min=salary_min,
                salary_max=salary_max,
                salary_currency=salary_currency,
                salary_period=salary_period,
                posted_at=detail.get("created"),
                external_id=job_id,
            )
