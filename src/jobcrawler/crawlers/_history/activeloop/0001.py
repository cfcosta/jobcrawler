
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

_APPLY_ROOT = "https://app.dover.com/apply/Activeloop"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job"

_NAMED_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "hellip": "...",
    "ldquo": '"',
    "lsquo": "'",
    "lt": "<",
    "nbsp": " ",
    "ndash": "-",
    "quot": '"',
    "rdquo": '"',
    "rsquo": "'",
}


def _decode_entity(match):
    value = match.group(1)
    if value.startswith("#x") or value.startswith("#X"):
        try:
            return chr(int(value[2:], 16))
        except ValueError:
            return match.group(0)
    if value.startswith("#"):
        try:
            return chr(int(value[1:]))
        except ValueError:
            return match.group(0)
    return _NAMED_ENTITIES.get(value, match.group(0))


def _html_to_text(value):
    text = value or ""
    text = re.sub(
        r"(?is)<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>",
        " ",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&(#(?:[xX][0-9A-Fa-f]+|[0-9]+)|[A-Za-z]+);", _decode_entity, text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _response_json(response):
    return json.loads(response)


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = _response_json(ctx.fetch(SITE))

    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True or listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = _response_json(ctx.fetch(f"{_DETAIL_ROOT}/{job_id}"))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            source_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                name
                for name in (_location_name(item) for item in source_locations)
                if name
            ]
            primary = next(
                (
                    _location_name(item)
                    for item in source_locations
                    if item.get("is_primary") and _location_name(item)
                ),
                None,
            )
            location = detail.get("location") or primary or (
                locations[0] if locations else None
            )
            if not location:
                continue

            apply_url = f"{_APPLY_ROOT}/{job_id}/"
            values = {
                "url": apply_url,
                "title": title,
                "company": detail.get("client_name") or "Activeloop",
                "location": location,
                "locations": locations or None,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": detail.get("workplace_type")
                or listed.get("workplace_type"),
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            if compensation.get("open_to_sharing_comp") is True:
                values["salary_min"] = compensation.get("lower_bound")
                values["salary_max"] = compensation.get("upper_bound")
                values["salary_currency"] = compensation.get("currency_code")
                values["salary_period"] = compensation.get("salary_range_type")

            values["employment_type"] = compensation.get("employment_type")

            yield Job(
                **{
                    key: value
                    for key, value in values.items()
                    if value is not None
                }
            )
