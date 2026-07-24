
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _body(response):
    if isinstance(response, str):
        return response
    text = response.text
    return text() if callable(text) else text


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(_body(ctx.fetch(SITE + "?format=json")))

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
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(_body(ctx.fetch(detail_url)))

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            location_items = (
                detail.get("locations")
                or listing.get("locations")
                or []
            )
            locations = []
            primary = None
            for item in location_items:
                name = _location_name(item)
                if not name:
                    continue
                locations.append(name)
                if item.get("is_primary"):
                    primary = name

            location = primary
            if not location and locations:
                location = locations[0]
            if not location:
                location = detail.get("location")

            description = _plain_text(
                detail.get("user_provided_description")
            )
            title = detail.get("title") or listing.get("title")
            if not title or not location or not description:
                continue

            compensation = detail.get("compensation") or {}
            kwargs = {
                "url": apply_url,
                "title": title,
                "company": detail.get("client_name") or "Activeloop",
                "location": location,
                "locations": locations or None,
                "description": description,
                "source": "Dover",
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": (
                    detail.get("workplace_type")
                    or listing.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "salary_min": compensation.get("lower_bound"),
                "salary_max": compensation.get("upper_bound"),
                "salary_currency": compensation.get("currency_code"),
                "salary_period": compensation.get("salary_range_type"),
                "posted_at": detail.get("created"),
            }
            kwargs = {
                key: value
                for key, value in kwargs.items()
                if value is not None
            }
            yield Job(**kwargs)
