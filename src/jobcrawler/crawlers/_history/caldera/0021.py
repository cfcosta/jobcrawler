import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_APPLY_ROOT = "https://app.dover.com/apply/caldera/"
_DETAIL_ROOT = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</\s*(?:p|div|li|h[1-6]|section|article|tr|ul|ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = json.loads(ctx.fetch(page_url))

        for listed in page.get("results", []):
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = json.loads(ctx.fetch(_DETAIL_ROOT + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            if not description:
                continue

            location_records = detail.get("locations") or listed.get("locations") or []
            locations = [
                record["name"]
                for record in location_records
                if isinstance(record, dict) and record.get("name")
            ]
            location = detail.get("location") or (locations[0] if locations else None)
            if not location:
                continue

            apply_url = _APPLY_ROOT + job_id + "/"
            fields = {
                "url": apply_url,
                "title": detail.get("title") or listed.get("title"),
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": detail.get("id") or job_id,
                "apply_url": apply_url,
                "workplace_type": detail.get("workplace_type") or listed.get("workplace_type"),
                "posted_at": detail.get("created"),
            }
            if locations:
                fields["locations"] = locations

            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                if lower is not None:
                    fields["salary_min"] = lower
                if upper is not None:
                    fields["salary_max"] = upper
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            if detail.get("compensation_details"):
                fields["salary"] = detail["compensation_details"]

            yield Job(**{
                key: value for key, value in fields.items()
                if value is not None
            })

        page_url = page.get("next")
