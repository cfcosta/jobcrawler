import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

COMPANY = "Activeloop"
APPLY_BASE = "https://app.dover.com/apply/Activeloop"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job"


def _html_to_text(value):
    value = unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tr|ul)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        line = re.sub(r"\s+", " ", unescape(line)).strip()
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
            if listed.get("is_published") is not True:
                continue
            if listed.get("is_sample") is not False:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = json.loads(ctx.fetch(DETAIL_BASE + "/" + job_id))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = _location_name(item)
                if not name:
                    continue
                locations.append(name)
                if item.get("is_primary"):
                    primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            compensation = detail.get("compensation") or {}
            share_salary = compensation.get("open_to_sharing_comp") is True
            apply_url = APPLY_BASE + "/" + job_id + "/"

            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": COMPANY,
                "source": SITE,
                "location": location,
                "locations": locations or None,
                "description": description,
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "salary_min": compensation.get("lower_bound") if share_salary else None,
                "salary_max": compensation.get("upper_bound") if share_salary else None,
                "salary_currency": compensation.get("currency_code") if share_salary else None,
                "salary_period": compensation.get("salary_range_type") if share_salary else None,
            }
            yield Job(**{key: value for key, value in fields.items() if value is not None})
