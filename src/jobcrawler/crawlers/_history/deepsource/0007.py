import json
import re
from datetime import datetime
from html import unescape
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job


SITE = 'https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_ORG_ID = "02322923-27a3-439d-aadc-865046d47d15"
_API_ROOT = "https://app.dover.com/api/v1"
_JOBS_URL = _API_ROOT + "/careers-page/" + _ORG_ID + "/jobs"
_DETAIL_ROOT = _API_ROOT + "/inbound/application-portal-job/"
_APPLY_ROOT = "https://app.dover.com/apply/DeepSource/"


def _html_to_text(value):
    text = unescape(value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:p|div|li|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _posted_at(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def crawl(ctx):
    offset = 0
    limit = 50

    while True:
        query = urlencode({"limit": limit, "offset": offset})
        page = json.loads(ctx.fetch(f"{_JOBS_URL}?{query}"))
        summaries = page.get("results") or []

        for summary in summaries:
            if not summary.get("is_published") or summary.get("is_sample"):
                continue

            job_id = str(summary["id"])
            detail = json.loads(ctx.fetch(f"{_DETAIL_ROOT}{job_id}"))
            if not detail.get("active") or detail.get("is_private"):
                continue

            source_locations = detail.get("locations") or summary.get("locations") or []
            locations = [
                item["name"]
                for item in source_locations
                if item.get("name")
            ]
            description = _html_to_text(detail.get("user_provided_description"))
            apply_url = f"{_APPLY_ROOT}{job_id}/"

            fields = {
                "url": apply_url,
                "source": SITE,
                "title": detail.get("title") or summary["title"],
                "company": "DeepSource",
                "location": locations[0] if locations else None,
                "locations": locations,
                "description": description,
                "external_id": job_id,
                "apply_url": apply_url,
            }

            workplace_type = detail.get("workplace_type") or summary.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            posted_at = _posted_at(detail.get("created"))
            if posted_at is not None:
                fields["posted_at"] = posted_at

            yield Job(**fields)

        if not page.get("next") or not summaries:
            break
        offset += len(summaries)
