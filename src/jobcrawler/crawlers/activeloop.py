import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

COMPANY = "Activeloop"
APPLY_BASE = "https://app.dover.com/apply/Activeloop"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job"


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(
        r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
        "",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:p|div|section|article|header|footer|h[1-6]|ul|ol|li|"
        r"blockquote|pre|table|tr|br|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _location_name(location):
    option = location.get("location_option") or {}
    return option.get("display_name") or location.get("name")


def crawl(ctx):
    separator = "&" if "?" in SITE else "?"
    groups = json.loads(ctx.fetch(SITE + separator + "format=json"))

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail = json.loads(ctx.fetch(DETAIL_BASE + "/" + job_id))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            title = detail.get("title") or listing.get("title")
            description = _text(detail.get("user_provided_description"))
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = _location_name(item)
                if not name:
                    continue
                locations.append(name)
                if item.get("is_primary") and primary is None:
                    primary = name

            location = detail.get("location") or primary
            if location is None and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = APPLY_BASE + "/" + job_id + "/"
            fields = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": title,
                "company": COMPANY,
                "location": location,
                "locations": locations or None,
                "description": description,
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type") or listing.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            if compensation.get("open_to_sharing_comp") is not False:
                fields["salary_min"] = compensation.get("lower_bound")
                fields["salary_max"] = compensation.get("upper_bound")
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")
            fields["employment_type"] = compensation.get("employment_type")

            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None
            })
