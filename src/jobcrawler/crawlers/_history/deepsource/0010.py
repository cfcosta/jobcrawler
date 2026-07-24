import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_API_ROOT = "https://app.dover.com"
_COMPANY = "DeepSource"
_COMPANY_SLUG = "DeepSource"


def _plain_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _page_url(offset):
    return (
        f"{_API_ROOT}/api/v1/careers-page/"
        "02322923-27a3-439d-aadc-865046d47d15/jobs"
        f"?limit=50&offset={offset}"
    )


def crawl(ctx):
    offset = 0
    seen_ids = set()

    while True:
        page = json.loads(ctx.fetch(_page_url(offset)))
        results = page.get("results") or []

        for summary in results:
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen_ids
                or summary.get("is_published") is not True
                or summary.get("is_sample") is True
            ):
                continue
            seen_ids.add(job_id)

            detail_url = (
                f"{_API_ROOT}/api/v1/inbound/application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            raw_locations = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = detail.get("location") or (
                locations[0] if locations else None
            )
            apply_url = f"{_API_ROOT}/apply/{_COMPANY_SLUG}/{job_id}/"

            fields = {
                "url": apply_url,
                "source": SITE,
                "apply_url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": _COMPANY,
                "location": location,
                "description": _plain_text(
                    detail.get("user_provided_description")
                ),
                "external_id": job_id,
                "workplace_type": (
                    detail.get("workplace_type")
                    or summary.get("workplace_type")
                ),
                "posted_at": detail.get("created"),
            }
            if locations:
                fields["locations"] = locations

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if (
                compensation.get("open_to_sharing_comp") is True
                and (lower is not None or upper is not None)
            ):
                if lower is not None:
                    fields["salary_min"] = lower
                if upper is not None:
                    fields["salary_max"] = upper
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )

        if not page.get("next") or not results:
            break
        offset += len(results)
