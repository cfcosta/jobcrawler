
import html
import json
import re

from jobcrawler.runtime.contract import Job
from urllib.parse import urljoin

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

CLIENT_SLUG = "caldera"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|h[1-6]|li|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _location_names(record):
    names = []
    for item in record.get("locations") or []:
        name = item.get("name")
        if name and name not in names:
            names.append(name)
    return names


def crawl(ctx):
    next_url = SITE
    seen = set()

    while next_url:
        page = json.loads(ctx.fetch(next_url))
        for listed in page.get("results") or []:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen
                or not listed.get("is_published")
                or listed.get("is_sample")
            ):
                continue
            seen.add(job_id)

            detail = json.loads(ctx.fetch(DETAIL_BASE + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

            locations = _location_names(detail) or _location_names(listed)
            location = detail.get("location") or (locations[0] if locations else None)
            compensation = detail.get("compensation") or {}
            posting_url = (
                "https://app.dover.com/apply/"
                + CLIENT_SLUG
                + "/"
                + job_id
                + "/"
            )

            fields = {
                "url": posting_url,
                "source": SITE,
                "apply_url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Caldera",
                "location": location,
                "description": description,
                "external_id": job_id,
                "workplace_type": detail.get("workplace_type")
                    or listed.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
            }
            if locations:
                fields["locations"] = locations

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                fields["salary_min"] = lower
                fields["salary_max"] = upper
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")
            if detail.get("compensation_details"):
                fields["salary"] = detail["compensation_details"]

            yield Job(**{key: value for key, value in fields.items()
                         if value is not None})

        candidate = page.get("next")
        next_url = urljoin(next_url, candidate) if candidate else None
