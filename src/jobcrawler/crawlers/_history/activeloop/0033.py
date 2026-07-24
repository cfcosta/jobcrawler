
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
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    board_response = ctx.fetch(SITE + "?format=json")
    if not board_response:
        return

    try:
        groups = json.loads(board_response)
    except (TypeError, ValueError):
        return

    for group in groups:
        for listing in group.get("jobs") or []:
            if listing.get("is_published") is not True:
                continue
            if listing.get("is_sample") is not False:
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail_response = ctx.fetch(_DETAIL_BASE + job_id)
            if not detail_response:
                continue

            try:
                detail = json.loads(detail_response)
            except (TypeError, ValueError):
                continue

            if detail.get("active") is False:
                continue

            title = detail.get("title") or listing.get("title")
            description = _plain_text(detail.get("user_provided_description"))
            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = [
                name
                for name in map(_location_name, raw_locations)
                if name
            ]
            primary = next(
                (
                    _location_name(item)
                    for item in raw_locations
                    if item.get("is_primary")
                ),
                None,
            )
            location = (
                detail.get("location")
                or primary
                or (locations[0] if locations else None)
            )

            if not title or not description or not location:
                continue

            apply_url = _APPLY_BASE + job_id + "/"
            fields = {
                "url": apply_url,
                "title": title,
                "company": _COMPANY,
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
                or listing.get("workplace_type")
            )
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
