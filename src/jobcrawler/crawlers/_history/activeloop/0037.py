
import json
from jobcrawler.runtime.contract import Job
import re
from html import unescape

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|li|h[1-6]|div|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _json(response):
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    if isinstance(response, str):
        return json.loads(response)
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        value = response.text
        return json.loads(value() if callable(value) else value)
    raise TypeError("Unsupported fetch response")


def crawl(ctx):
    groups = _json(ctx.fetch(SITE))
    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True or listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue
            url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            detail_url = f"https://app.dover.com/api/v1/inbound/application-portal-job/{job_id}"
            detail = _json(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                if not isinstance(item, dict):
                    continue
                option = item.get("location_option") or {}
                name = option.get("display_name") or item.get("name")
                if name:
                    locations.append(name)
                    if item.get("is_primary") is True:
                        primary = name
            location = primary or (locations[0] if locations else None) or detail.get("location")
            description = _text(detail.get("user_provided_description"))
            if not description or not location:
                continue

            values = {
                "url": url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": "Dover",
                "external_id": job_id,
                "apply_url": url,
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "posted_at": detail.get("created"),
            }
            if locations:
                values["locations"] = locations

            comp = detail.get("compensation") or {}
            values["employment_type"] = comp.get("employment_type")
            values["salary_min"] = comp.get("lower_bound")
            values["salary_max"] = comp.get("upper_bound")
            values["salary_currency"] = comp.get("currency_code")
            values["salary_period"] = comp.get("salary_range_type")

            yield Job(**{key: value for key, value in values.items() if value is not None})
