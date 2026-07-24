
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value)
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
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
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue
            description = _plain_text(description_html)
            if not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            for item in raw_locations:
                name = _location_name(item)
                if name:
                    locations.append(name)

            primary_location = None
            for item in raw_locations:
                if item.get("is_primary"):
                    primary_location = _location_name(item)
                    if primary_location:
                        break
            if not primary_location:
                primary_location = detail.get("location")
            if not primary_location and locations:
                primary_location = locations[0]
            if not primary_location:
                continue

            values = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": detail.get("client_name"),
                "location": primary_location,
                "locations": locations or None,
                "description": description,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            if compensation.get("lower_bound") is not None:
                values["salary_min"] = compensation.get("lower_bound")
            if compensation.get("upper_bound") is not None:
                values["salary_max"] = compensation.get("upper_bound")
            if compensation.get("currency_code"):
                values["salary_currency"] = compensation.get("currency_code")
            if compensation.get("salary_range_type"):
                values["salary_period"] = compensation.get("salary_range_type")
            if compensation.get("employment_type"):
                values["employment_type"] = compensation.get("employment_type")

            yield Job(**{
                key: value
                for key, value in values.items()
                if value is not None and value != ""
            })
