
import html
import json
import re
from urllib.parse import quote

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _add_if_present(record, key, value):
    if value is not None and value != "":
        record[key] = value


def crawl(ctx):
    next_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while next_url and next_url not in seen_pages:
        seen_pages.add(next_url)
        page = json.loads(ctx.fetch(next_url))

        for summary in page.get("results") or []:
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen_jobs
                or summary.get("is_published") is not True
                or summary.get("is_sample") is True
            ):
                continue
            seen_jobs.add(job_id)

            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                "application-portal-job/" + quote(str(job_id), safe="")
            )
            detail = json.loads(ctx.fetch(detail_url))
            if detail.get("active") is not True or detail.get("is_private") is True:
                continue

            title = detail.get("title") or summary.get("title")
            description = _plain_text(detail.get("user_provided_description"))
            if not title or not description:
                continue

            location_objects = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name")
                for item in location_objects
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_objects
                    if isinstance(item, dict)
                    and item.get("is_primary") is True
                    and item.get("name")
                ),
                None,
            )
            location = detail.get("location") or primary
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            company = detail.get("client_name") or "DeepSource"
            apply_url = (
                "https://app.dover.com/apply/"
                + quote(company, safe="")
                + "/"
                + quote(str(job_id), safe="")
                + "/"
            )
            record = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "external_id": str(job_id),
                "source": SITE,
            }

            if locations:
                record["locations"] = locations

            _add_if_present(
                record,
                "workplace_type",
                detail.get("workplace_type") or summary.get("workplace_type"),
            )
            _add_if_present(record, "posted_at", detail.get("created"))

            compensation = detail.get("compensation") or {}
            _add_if_present(
                record,
                "employment_type",
                compensation.get("employment_type"),
            )

            if compensation.get("open_to_sharing_comp") is True:
                lower = compensation.get("lower_bound")
                upper = compensation.get("upper_bound")
                if lower is not None or upper is not None:
                    _add_if_present(record, "salary_min", lower)
                    _add_if_present(record, "salary_max", upper)
                    _add_if_present(
                        record,
                        "salary_currency",
                        compensation.get("currency_code"),
                    )
                    _add_if_present(
                        record,
                        "salary_period",
                        compensation.get("salary_range_type"),
                    )

            _add_if_present(record, "salary", detail.get("compensation_details"))
            yield Job(**record)

        next_url = page.get("next")
