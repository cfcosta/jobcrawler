
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []

_APPLY_BASE = "https://app.dover.com/apply/Activeloop/"
_DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"

_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "gt": ">",
    "hellip": "...",
    "lt": "<",
    "nbsp": " ",
    "quot": '"',
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
    return _ENTITIES.get(value, match.group(0))


def _plain_text(value):
    if not value:
        return None

    text = re.sub(
        r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<\s*(br|p|div|h[1-6]|ul|ol|blockquote|section|article|tr|table|hr)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<\s*/\s*(p|div|h[1-6]|li|ul|ol|blockquote|section|article|tr|table)\s*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&([A-Za-z]+|#[0-9]+|#[xX][0-9A-Fa-f]+);", _decode_entity, text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    merged = []
    pending_bullet = False
    for line in lines:
        if line == "-":
            pending_bullet = True
        elif pending_bullet:
            merged.append("- " + line)
            pending_bullet = False
        else:
            merged.append(line)
    return "\n".join(merged) or None


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))
    seen = set()

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)

            detail = json.loads(ctx.fetch(_DETAIL_BASE + job_id))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            title = detail.get("title") or listing.get("title")
            company = detail.get("client_name")
            description = _plain_text(detail.get("user_provided_description"))
            if not title or not company or not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = []
            primary = None
            for item in raw_locations:
                name = _location_name(item)
                if name:
                    locations.append(name)
                    if item.get("is_primary") and primary is None:
                        primary = name

            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = _APPLY_BASE + job_id + "/"
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = detail.get("workplace_type") or listing.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if lower is not None:
                    fields["salary_min"] = lower
                if upper is not None:
                    fields["salary_max"] = upper
                if currency:
                    fields["salary_currency"] = currency
                if period:
                    fields["salary_period"] = period

            yield Job(**fields)
