
import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_COMPANY = "DeepSource"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job/"
_APPLY_ROOT = "https://app.dover.com/apply/DeepSource/"


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/li|/h[1-6]|/div|/section|/article|/tr|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(html.unescape(line).split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _json_response(response):
    return json.loads(response)


def crawl(ctx):
    next_url = SITE
    seen_pages = set()
    summaries = []

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        page = _json_response(ctx.fetch(next_url))
        summaries.extend(page.get("results") or [])
        candidate = page.get("next")
        next_url = urljoin(next_url, candidate) if candidate else None

    seen_ids = set()
    for summary in summaries:
        job_id = summary.get("id")
        if (
            not job_id
            or job_id in seen_ids
            or summary.get("is_published") is not True
            or summary.get("is_sample") is True
        ):
            continue
        seen_ids.add(job_id)

        detail = _json_response(ctx.fetch(_DETAIL_ROOT + job_id))
        if detail.get("active") is not True or detail.get("is_private") is True:
            continue

        title = detail.get("title") or summary.get("title")
        description = _plain_text(detail.get("user_provided_description"))
        if not title or not description:
            continue

        raw_locations = detail.get("locations") or summary.get("locations") or []
        locations = [
            item.get("name")
            for item in raw_locations
            if isinstance(item, dict) and item.get("name")
        ]
        location = detail.get("location") or (locations[0] if locations else None)
        if not location:
            continue

        posting_url = _APPLY_ROOT + job_id + "/"
        values = {
            "url": posting_url,
            "title": title,
            "company": _COMPANY,
            "location": location,
            "source": SITE,
            "description": description,
            "external_id": job_id,
            "apply_url": posting_url,
        }

        if locations:
            values["locations"] = locations

        workplace_type = detail.get("workplace_type") or summary.get("workplace_type")
        if workplace_type:
            values["workplace_type"] = workplace_type

        if detail.get("created"):
            values["posted_at"] = detail["created"]

        compensation = detail.get("compensation") or {}
        if compensation.get("employment_type"):
            values["employment_type"] = compensation["employment_type"]

        lower = compensation.get("lower_bound")
        upper = compensation.get("upper_bound")
        if compensation.get("open_to_sharing_comp") is True and (
            lower is not None or upper is not None
        ):
            if lower is not None:
                values["salary_min"] = lower
            if upper is not None:
                values["salary_max"] = upper
            if compensation.get("currency_code"):
                values["salary_currency"] = compensation["currency_code"]
            if compensation.get("salary_range_type"):
                values["salary_period"] = compensation["salary_range_type"]

        yield Job(**values)
