
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _text(value):
    value = value or ""
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<script\b[^>]*>.*?</script>", "", value, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", "", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _location_name(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))

    for group in groups:
        for listed in group.get("jobs") or []:
            if listed.get("is_published") is not True or listed.get("is_sample") is True:
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

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
                    break
            location = (
                detail.get("location")
                or primary
                or (locations[0] if locations else "Unspecified")
            )

            slug = detail.get("client_name") or "Activeloop"
            posting_url = f"https://app.dover.com/apply/{slug}/{job_id}/"

            fields = {
                "url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Activeloop",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": posting_url,
            }

            if locations:
                fields["locations"] = locations
            workplace = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace:
                fields["workplace_type"] = workplace
            if group.get("name") and group.get("name") != "Ungrouped":
                fields["department"] = group["name"]
            if detail.get("created"):
                fields["posted_at"] = detail["created"]

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]
            if compensation.get("open_to_sharing_comp") is True:
                if compensation.get("lower_bound") is not None:
                    fields["salary_min"] = compensation["lower_bound"]
                if compensation.get("upper_bound") is not None:
                    fields["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            yield Job(**fields)
