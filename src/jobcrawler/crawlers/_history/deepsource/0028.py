
import json
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _json(response):
    if hasattr(response, "json"):
        return response.json()
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    if not isinstance(response, str) and hasattr(response, "text"):
        response = response.text
    return json.loads(response)


def _plain_text(value):
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/section|/article|/tr|/ul|/ol)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = _json(ctx.fetch(page_url))

        for listing in page.get("results", []):
            if not listing.get("is_published") or listing.get("is_sample"):
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + str(job_id)
            )
            detail = _json(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue
            description = _plain_text(description_html)
            if not description:
                continue

            raw_locations = detail.get("locations") or listing.get("locations") or []
            locations = [
                item["name"]
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = detail.get("location") or (locations[0] if locations else None)
            if not location:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            kwargs = {
                "url": apply_url,
                "title": detail.get("title") or listing.get("title"),
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": apply_url,
            }
            if locations:
                kwargs["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or listing.get("workplace_type")
            )
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                kwargs["employment_type"] = compensation["employment_type"]
            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]

            yield kwargs

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
