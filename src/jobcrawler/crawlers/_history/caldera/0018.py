import json
from urllib.parse import urljoin

import html
import re
from jobcrawler.runtime.contract import Job

SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|"
    r"li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")

def _html_to_text(value):
    value = _BLOCK_TAG_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)

_BASE = "https://app.dover.com"
_COMPANY = "Caldera"
_COMPANY_SLUG = "caldera"


def crawl(ctx):
    next_url = SITE
    seen_pages = set()

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        page = json.loads(ctx.fetch(next_url))

        for item in page.get("results") or []:
            if not item.get("is_published") or item.get("is_sample"):
                continue

            job_id = item.get("id")
            if not job_id:
                continue

            detail_url = f"{_BASE}/api/v1/inbound/application-portal-job/{job_id}"
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue

            source_locations = detail.get("locations") or item.get("locations") or []
            locations = [
                location["name"]
                for location in source_locations
                if isinstance(location, dict) and location.get("name")
            ]
            location = detail.get("location") or (locations[0] if locations else None)
            if not location:
                continue

            canonical_url = f"{_BASE}/apply/{_COMPANY_SLUG}/{job_id}/"
            kwargs = {
                "url": canonical_url,
                "title": detail.get("title") or item.get("title"),
                "company": _COMPANY,
                "location": location,
                "description": _html_to_text(description_html),
                "source": SITE,
                "external_id": job_id,
                "apply_url": canonical_url,
            }

            if locations:
                kwargs["locations"] = locations

            workplace_type = detail.get("workplace_type") or item.get("workplace_type")
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                kwargs["employment_type"] = compensation["employment_type"]

            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                if lower is not None:
                    kwargs["salary_min"] = lower
                if upper is not None:
                    kwargs["salary_max"] = upper
                if compensation.get("currency_code"):
                    kwargs["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    kwargs["salary_period"] = compensation["salary_range_type"]
                if detail.get("compensation_details"):
                    kwargs["salary"] = detail["compensation_details"]

            if kwargs["title"] and kwargs["description"]:
                yield Job(**kwargs)

        continuation = page.get("next")
        next_url = urljoin(next_url, continuation) if continuation else None
