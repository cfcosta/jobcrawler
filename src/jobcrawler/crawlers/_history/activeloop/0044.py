
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _get_json(ctx, url):
    response = ctx.fetch(url)
    if isinstance(response, bytes):
        response = response.decode("utf-8", "replace")
    return json.loads(response)


def _html_to_text(value):
    if not value:
        return None

    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</\s*(?:p|div|li|h[1-6]|tr|section|article)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location_name(location):
    option = location.get("location_option") or {}
    return (
        option.get("display_name")
        or location.get("name")
        or option.get("city")
    )


def crawl(ctx):
    groups = _get_json(ctx, SITE)

    for group in groups:
        for summary in group.get("jobs") or []:
            if summary.get("is_published") is not True:
                continue
            if summary.get("is_sample") is not False:
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            api_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = _get_json(ctx, api_url)
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or summary.get("locations") or []
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

            fields = {
                "url": url,
                "source": SITE,
                "apply_url": url,
                "title": detail.get("title") or summary.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "external_id": job_id,
            }
            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or summary.get("workplace_type")
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
                salary_min = compensation.get("lower_bound")
                salary_max = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if salary_min is not None:
                    fields["salary_min"] = salary_min
                if salary_max is not None:
                    fields["salary_max"] = salary_max
                if currency:
                    fields["salary_currency"] = currency
                if period:
                    fields["salary_period"] = period

            group_name = group.get("name")
            if group_name and group_name.lower() != "ungrouped":
                fields["department"] = group_name

            yield Job(**fields)
