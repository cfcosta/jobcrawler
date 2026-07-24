
import json
import re
import html

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = value or ""
    value = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


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

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _html_to_text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                name
                for name in (_location_name(item) for item in raw_locations)
                if name
            ]
            primary = next(
                (
                    _location_name(item)
                    for item in raw_locations
                    if item.get("is_primary") and _location_name(item)
                ),
                None,
            )
            location = primary or detail.get("location")
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = "https://app.dover.com/apply/Activeloop/" + job_id + "/"
            fields = {
                "url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": "Dover",
                "external_id": job_id,
                "apply_url": apply_url,
                "workplace_type": (
                    detail.get("workplace_type") or listed.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }
            if locations:
                fields["locations"] = locations

            compensation = detail.get("compensation") or {}
            fields["employment_type"] = compensation.get("employment_type")
            if compensation.get("open_to_sharing_comp") is True:
                fields["salary_min"] = compensation.get("lower_bound")
                fields["salary_max"] = compensation.get("upper_bound")
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )
