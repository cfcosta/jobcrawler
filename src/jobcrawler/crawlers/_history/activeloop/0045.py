
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
        r"(?i)<\s*(?:br|hr)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _response_text(response):
    return response.text if hasattr(response, "text") else response


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(_response_text(ctx.fetch(SITE + "?format=json")))
    for group in groups:
        department = group.get("name")
        if department == "Ungrouped":
            department = None

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
            detail = json.loads(_response_text(ctx.fetch(detail_url)))
            if detail.get("active") is not True:
                continue
            if detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            if not description:
                description = _html_to_text(detail.get("user_facing_description"))
            if not description:
                continue

            source_locations = (
                detail.get("locations")
                or listed.get("locations")
                or []
            )
            locations = []
            primary = None
            for item in source_locations:
                name = _location_name(item)
                if name:
                    locations.append(name)
                    if item.get("is_primary") and not primary:
                        primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = (
                "https://app.dover.com/apply/Activeloop/"
                + job_id
                + "/"
            )
            fields = {
                "url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
            }
            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type")
                or listed.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type
            if department:
                fields["department"] = department
            if detail.get("created"):
                fields["posted_at"] = detail.get("created")

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                lower_bound = compensation.get("lower_bound")
                upper_bound = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if lower_bound is not None:
                    fields["salary_min"] = lower_bound
                if upper_bound is not None:
                    fields["salary_max"] = upper_bound
                if currency:
                    fields["salary_currency"] = currency
                if period:
                    fields["salary_period"] = period

            yield Job(**fields)
