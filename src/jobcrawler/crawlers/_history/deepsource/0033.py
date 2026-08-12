import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _response_text(response):
    return response.text if hasattr(response, "text") else str(response)


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<\s*li\b[^>]*>\s*<\s*p\b[^>]*>",
        "\n- ",
        text,
        flags=re.I,
    )
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<\s*/\s*p\s*>\s*<\s*/\s*li\s*>", "\n", text, flags=re.I)
    text = re.sub(
        r"<\s*/?\s*(?:p|div|section|article|h[1-6]|ul|ol|br|tr|table|blockquote)\b[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    limit = 50
    offset = 0

    while True:
        page_url = (
            "https://app.dover.com/api/v1/careers-page/"
            "02322923-27a3-439d-aadc-865046d47d15/jobs"
            "?limit=" + str(limit) + "&offset=" + str(offset)
        )
        page = json.loads(_response_text(ctx.fetch(page_url)))

        for listed in page.get("results", []):
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(_response_text(ctx.fetch(detail_url)))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            location_items = detail.get("locations") or listed.get("locations") or []
            locations = [
                item.get("name")
                for item in location_items
                if item.get("name")
            ]
            location = locations[0] if locations else detail.get("location")
            apply_url = "https://app.dover.com/apply/DeepSource/" + job_id + "/"

            values = {
                "url": apply_url,
                "title": title,
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
                "locations": locations or None,
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "posted_at": detail.get("created"),
            }

            compensation = detail.get("compensation") or {}
            values["employment_type"] = compensation.get("employment_type")
            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if compensation.get("open_to_sharing_comp") and (lower is not None or upper is not None):
                values["salary_min"] = lower
                values["salary_max"] = upper
                values["salary_currency"] = compensation.get("currency_code")
                values["salary_period"] = compensation.get("salary_range_type")
            if detail.get("compensation_details"):
                values["salary"] = detail.get("compensation_details")

            yield Job(**{key: value for key, value in values.items() if value is not None})

        results = page.get("results") or []
        offset += len(results)
        if not results or offset >= page.get("count", offset):
            break
