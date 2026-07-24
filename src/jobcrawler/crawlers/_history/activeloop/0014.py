import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value)
    value = re.sub(r"(?i)<li[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|tr|ul)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", "", value)
    value = html.unescape(value)
    lines = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    result = "\n".join(lines).strip()
    return result or None


def _json(response):
    if hasattr(response, "json"):
        return response.json()
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    return json.loads(response)


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = _json(ctx.fetch(SITE + "?format=json"))

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
            detail = _json(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            apply_url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            location_items = detail.get("locations") or listed.get("locations") or []
            locations = []
            for item in location_items:
                name = _location_name(item)
                if name and name not in locations:
                    locations.append(name)

            primary = None
            for item in location_items:
                if item.get("is_primary"):
                    primary = _location_name(item)
                    if primary:
                        break
            location = primary or (locations[0] if locations else None)

            compensation = detail.get("compensation") or {}
            department = group.get("name")
            if department == "Ungrouped":
                department = None

            kwargs = {
                "url": apply_url,
                "source": SITE,
                "title": detail.get("title") or listed.get("title"),
                "company": detail.get("client_name") or "Activeloop",
                "location": location,
                "locations": locations or None,
                "description": _text(detail.get("user_provided_description")),
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "department": department,
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "apply_url": apply_url,
            }
            if compensation.get("open_to_sharing_comp") is True:
                kwargs.update(
                    salary_min=compensation.get("lower_bound"),
                    salary_max=compensation.get("upper_bound"),
                    salary_currency=compensation.get("currency_code"),
                    salary_period=compensation.get("salary_range_type"),
                )

            yield Job(**{key: value for key, value in kwargs.items() if value is not None})
