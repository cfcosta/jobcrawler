import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value)
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE))

    for group in groups:
        for board_job in group.get("jobs") or []:
            if board_job.get("is_published") is not True:
                continue
            if board_job.get("is_sample") is not False:
                continue

            job_id = board_job.get("id")
            if not job_id:
                continue

            url = f"https://app.dover.com/apply/Activeloop/{job_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                f"{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))

            if detail.get("active") is not True:
                continue
            if detail.get("is_private") is True:
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue
            description = _plain_text(description_html)
            if not description:
                continue

            source_locations = (
                detail.get("locations")
                or board_job.get("locations")
                or []
            )
            locations = []
            primary_location = None
            for item in source_locations:
                location_option = item.get("location_option") or {}
                name = location_option.get("display_name") or item.get("name")
                if name:
                    locations.append(name)
                    if item.get("is_primary") is True and primary_location is None:
                        primary_location = name

            location = primary_location or (locations[0] if locations else None)
            if not location:
                continue

            fields = {
                "url": url,
                "apply_url": url,
                "title": detail.get("title") or board_job.get("title"),
                "company": detail.get("client_name") or "Activeloop",
                "location": location,
                "locations": locations,
                "description": description,
                "source": SITE,
                "external_id": job_id,
            }

            workplace_type = (
                detail.get("workplace_type")
                or board_job.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if compensation.get("open_to_sharing_comp") is True:
                lower_bound = compensation.get("lower_bound")
                upper_bound = compensation.get("upper_bound")
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")

                if lower_bound is not None:
                    fields["salary_min"] = lower_bound
                if upper_bound is not None:
                    fields["salary_max"] = upper_bound
                if currency:
                    fields["salary_currency"] = currency
                if period:
                    fields["salary_period"] = period

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            department = group.get("name")
            if department and department.lower() != "ungrouped":
                fields["department"] = department

            yield Job(**fields)
