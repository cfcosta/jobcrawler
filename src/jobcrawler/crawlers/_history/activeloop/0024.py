import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location_name(location):
    option = location.get("location_option") or {}
    return option.get("display_name") or location.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listed in group.get("jobs") or []:
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
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
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
                detail.get("location")
                or primary
                or (locations[0] if locations else None)
            )
            if not location:
                continue

            posting_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            fields = {
                "url": posting_url,
                "source": SITE,
                "apply_url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": location,
                "locations": locations or None,
                "description": description,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listed.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }

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

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )
