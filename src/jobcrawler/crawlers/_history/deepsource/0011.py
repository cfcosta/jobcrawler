
import json
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(?:p|li|ul|ol|h[1-6]|div)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _json(ctx, url):
    response = ctx.fetch(url)
    if isinstance(response, str):
        return json.loads(response)
    if isinstance(response, (bytes, bytearray)):
        return json.loads(response.decode("utf-8"))
    return response.json()


def crawl(ctx):
    next_url = SITE
    while next_url:
        page = _json(ctx, next_url)
        for item in page.get("results", []):
            if not item.get("is_published") or item.get("is_sample"):
                continue

            job_id = item.get("id")
            if not job_id:
                continue

            apply_url = f"https://app.dover.com/apply/DeepSource/{job_id}/"
            detail_url = f"https://app.dover.com/api/v1/inbound/application-portal-job/{job_id}"
            detail = _json(ctx, detail_url)
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text(detail.get("user_provided_description"))
            title = detail.get("title") or item.get("title")
            raw_locations = detail.get("locations") or item.get("locations") or []
            locations = [
                loc.get("name") or (loc.get("location_option") or {}).get("display_name")
                for loc in raw_locations
            ]
            locations = [value for value in locations if value]
            location = (
                next(
                    (
                        loc.get("name")
                        or (loc.get("location_option") or {}).get("display_name")
                        for loc in raw_locations
                        if loc.get("is_primary")
                    ),
                    None,
                )
                or (locations[0] if locations else None)
                or detail.get("location")
            )
            if not title or not description or not location:
                continue

            result = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
            }
            if locations:
                result["locations"] = locations

            workplace = detail.get("workplace_type") or item.get("workplace_type")
            if workplace:
                result["workplace_type"] = workplace

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                result["employment_type"] = employment_type
            if detail.get("created"):
                result["posted_at"] = detail["created"]

            if compensation.get("open_to_sharing_comp"):
                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                if lower is not None:
                    result["salary_min"] = lower
                if upper is not None:
                    result["salary_max"] = upper
                if (lower is not None or upper is not None) and compensation.get("currency_code"):
                    result["salary_currency"] = compensation["currency_code"]
                if (lower is not None or upper is not None) and compensation.get("salary_range_type"):
                    result["salary_period"] = compensation["salary_range_type"]

            yield result

        next_value = page.get("next")
        next_url = urljoin(next_url, next_value) if next_value else None
