
import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*(br|hr)\s*/?\s*>|<\s*/\s*(p|div|li|h[1-6]|section|article|tr|ul|ol)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _response_text(response):
    if hasattr(response, "text"):
        return response.text
    return str(response)


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = json.loads(_response_text(ctx.fetch(page_url)))

        for item in page.get("results", []):
            if not item.get("is_published") or item.get("is_sample"):
                continue

            job_id = item["id"]
            apply_url = f"https://app.dover.com/apply/caldera/{job_id}/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                f"application-portal-job/{job_id}"
            )
            detail = json.loads(_response_text(ctx.fetch(detail_url)))

            if not detail.get("active") or detail.get("is_private"):
                continue

            location_items = detail.get("locations") or item.get("locations") or []
            locations = [
                entry["name"]
                for entry in location_items
                if isinstance(entry, dict) and entry.get("name")
            ]
            primary = next(
                (
                    entry.get("name")
                    for entry in location_items
                    if isinstance(entry, dict)
                    and entry.get("is_primary")
                    and entry.get("name")
                ),
                None,
            )
            location = primary or (locations[0] if locations else None)
            compensation = detail.get("compensation") or {}

            kwargs = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or item.get("title"),
                "company": "Caldera",
                "location": location,
                "description": _html_to_text(
                    detail.get("user_provided_description")
                ),
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type") or item.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
            }
            if locations:
                kwargs["locations"] = locations

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None:
                kwargs["salary_min"] = lower
            if upper is not None:
                kwargs["salary_max"] = upper
            if lower is not None or upper is not None:
                if compensation.get("currency_code"):
                    kwargs["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    kwargs["salary_period"] = compensation["salary_range_type"]

            yield Job(
                **{
                    key: value
                    for key, value in kwargs.items()
                    if value is not None and value != ""
                }
            )

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
