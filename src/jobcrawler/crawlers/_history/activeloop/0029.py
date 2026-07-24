import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def clean_html(value):
    text = value or ""
    text = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|blockquote|pre|table|tr|br|hr)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    board_response = ctx.fetch(SITE + "?format=json")
    groups = json.loads(board_response)

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail_response = ctx.fetch(detail_url)
            detail = json.loads(detail_response)

            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = clean_html(detail.get("user_provided_description"))
            title = detail.get("title") or listing.get("title")
            company = detail.get("client_name") or "Activeloop"

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = location_name(item)
                if name:
                    locations.append(name)
                    if item.get("is_primary"):
                        primary = name

            location = primary
            if location is None and locations:
                location = locations[0]

            apply_url = (
                "https://app.dover.com/apply/Activeloop/" + job_id + "/"
            )
            fields = {
                "url": apply_url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or listing.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            if detail.get("created"):
                fields["posted_at"] = detail["created"]

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

            yield Job(**fields)
