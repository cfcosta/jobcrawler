import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

_COMPANY = "Activeloop"
_APPLY_BASE = "https://app.dover.com/apply/Activeloop/"
_DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _html_to_text(value):
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|header|li|main|ol|p|pre|section|table|tr|ul)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _location_names(job):
    names = []
    for item in job.get("locations") or []:
        option = item.get("location_option") or {}
        name = option.get("display_name") or item.get("name")
        if name and name not in names:
            names.append(name)
    return names


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE))

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

            title = detail.get("title") or listed.get("title")
            description_html = detail.get("user_provided_description")
            description = _html_to_text(description_html) if description_html else None
            locations = _location_names(detail) or _location_names(listed)
            location = detail.get("location") or (locations[0] if locations else None)

            if not title or not description or not location:
                continue

            apply_url = _APPLY_BASE + job_id + "/"
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "locations": locations or None,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            fields["employment_type"] = compensation.get("employment_type")
            if compensation.get("open_to_sharing_comp") is True:
                fields["salary_min"] = compensation.get("lower_bound")
                fields["salary_max"] = compensation.get("upper_bound")
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(**{key: value for key, value in fields.items() if value is not None})
