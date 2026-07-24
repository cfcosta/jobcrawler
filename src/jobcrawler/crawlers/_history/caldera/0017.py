
import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?is)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|"
        r"blockquote|div|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines) or None


def crawl(ctx):
    page_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = json.loads(ctx.fetch(page_url))

        for listed in page.get("results") or []:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen_jobs
                or not listed.get("is_published")
                or listed.get("is_sample")
            ):
                continue
            seen_jobs.add(job_id)

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            if not description:
                continue

            location_records = detail.get("locations") or listed.get("locations") or []
            locations = [
                record.get("name")
                for record in location_records
                if isinstance(record, dict) and record.get("name")
            ]
            primary = next(
                (
                    record.get("name")
                    for record in location_records
                    if isinstance(record, dict)
                    and record.get("is_primary")
                    and record.get("name")
                ),
                None,
            )
            location = (
                detail.get("location")
                or primary
                or (locations[0] if locations else None)
            )
            if not location:
                continue

            posting_url = f"https://app.dover.com/apply/caldera/{job_id}/"
            compensation = detail.get("compensation") or {}

            fields = {
                "url": posting_url,
                "source": SITE,
                "title": detail.get("title") or listed.get("title"),
                "company": "Caldera",
                "location": location,
                "locations": locations or None,
                "description": description,
                "workplace_type": (
                    detail.get("workplace_type") or listed.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "apply_url": posting_url,
            }

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                fields["salary_min"] = lower
                fields["salary_max"] = upper
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
