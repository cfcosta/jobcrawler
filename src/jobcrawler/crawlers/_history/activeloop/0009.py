
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/job-groups/03f9ea55-1b24-40b0-bda9-50d389b435fb/job-groups"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _display_location(item):
    option = item.get("location_option") or {}
    return option.get("display_name") or item.get("name")


def crawl(ctx):
    groups = json.loads(ctx.fetch(SITE + "?format=json"))
    seen = set()

    for group in groups:
        for listed in group.get("jobs") or []:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen
                or listed.get("is_published") is not True
                or listed.get("is_sample") is True
            ):
                continue
            seen.add(job_id)

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                name
                for name in (_display_location(item) for item in raw_locations)
                if name
            ]
            primary = next(
                (
                    _display_location(item)
                    for item in raw_locations
                    if item.get("is_primary") and _display_location(item)
                ),
                None,
            )
            location = primary or (locations[0] if locations else detail.get("location"))
            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

            apply_url = "https://app.dover.com/apply/Activeloop/" + job_id + "/"
            kwargs = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": detail.get("client_name"),
                "location": location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            if locations:
                kwargs["locations"] = locations
            if detail.get("workplace_type"):
                kwargs["workplace_type"] = detail["workplace_type"]
            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]
            if group.get("name") and group.get("name") != "Ungrouped":
                kwargs["department"] = group["name"]

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
