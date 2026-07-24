
import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_ORG_ID = "02322923-27a3-439d-aadc-865046d47d15"
_APPLY_ROOT = "https://app.dover.com/apply/DeepSource/"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(?:p|li|div|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    url = SITE
    seen_pages = set()
    while url and url not in seen_pages:
        seen_pages.add(url)
        page = json.loads(ctx.fetch(url))
        for summary in page.get("results", []):
            if not summary.get("is_published") or summary.get("is_sample"):
                continue

            job_id = summary.get("id")
            if not job_id:
                continue
            detail = json.loads(ctx.fetch(_DETAIL_ROOT + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in raw_locations
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = detail.get("location") or primary or (
                locations[0] if locations else None
            )
            apply_url = _APPLY_ROOT + job_id + "/"

            compensation = detail.get("compensation") or {}
            fields = {
                "url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": "Dover",
                "locations": locations or None,
                "workplace_type": detail.get("workplace_type")
                    or summary.get("workplace_type"),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "apply_url": apply_url,
            }

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if compensation.get("open_to_sharing_comp") and (
                lower is not None or upper is not None
            ):
                fields["salary_min"] = lower
                fields["salary_max"] = upper
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(**{key: value for key, value in fields.items()
                         if value is not None})

        next_url = page.get("next")
        url = urljoin(url, next_url) if next_url else None
