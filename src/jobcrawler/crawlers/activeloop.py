
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

_APPLY_ROOT = "https://app.dover.com/apply/Activeloop/"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _json(ctx, url):
    value = ctx.fetch(url)
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _description(value):
    value = re.sub(
        r"(?i)</?(?:p|div|section|article|h[1-6]|ul|ol|li|br|hr|"
        r"blockquote|pre|table|tr|address|main|header|footer)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = _json(ctx, SITE)

    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True:
                continue
            if listed.get("is_sample") is not False:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = _json(ctx, _DETAIL_ROOT + job_id)
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            raw_description = detail.get("user_provided_description")
            if not raw_description:
                continue
            description = _description(raw_description)
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
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
                if detail.get("workplace_type") == "REMOTE":
                    primary_location = "Remote"
                else:
                    primary_location = "Unspecified"

            posting_url = _APPLY_ROOT + job_id + "/"
            kwargs = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": detail.get("client_name") or "Activeloop",
                "location": primary_location,
                "description": description,
                "source": SITE,
                "external_id": detail.get("id") or job_id,
            }

            if locations:
                kwargs["locations"] = locations
            if detail.get("workplace_type"):
                kwargs["workplace_type"] = detail["workplace_type"]
            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                kwargs["employment_type"] = compensation["employment_type"]
            if compensation.get("open_to_sharing_comp") is True:
                if compensation.get("lower_bound") is not None:
                    kwargs["salary_min"] = compensation["lower_bound"]
                if compensation.get("upper_bound") is not None:
                    kwargs["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code"):
                    kwargs["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    kwargs["salary_period"] = compensation["salary_range_type"]

            yield Job(**kwargs)
