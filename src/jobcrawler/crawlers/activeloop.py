import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

_COMPANY = "Activeloop"
_APPLY_BASE = "https://app.dover.com/apply/Activeloop/"
_DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

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
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True or listed.get("is_sample") is not False:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = json.loads(ctx.fetch(_DETAIL_BASE + job_id))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [name for name in map(_location_name, raw_locations) if name]
            primary = None
            for item in raw_locations:
                if item.get("is_primary"):
                    primary = _location_name(item)
                    if primary:
                        break

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            url = _APPLY_BASE + job_id + "/"
            data = {
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": url,
            }

            if locations:
                data["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                data["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                data["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                data["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if lower is not None:
                    data["salary_min"] = lower
                if upper is not None:
                    data["salary_max"] = upper
                if currency:
                    data["salary_currency"] = currency
                if period:
                    data["salary_period"] = period

            yield Job(url=url, **data)
