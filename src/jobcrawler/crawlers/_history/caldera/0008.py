import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_API_ROOT = "https://app.dover.com"
_COMPANY = "Caldera"
_COMPANY_SLUG = "caldera"
_DETAIL_ROOT = _API_ROOT + "/api/v1/inbound/application-portal-job/"


def _plain_text(value):
    if not value:
        return None

    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>",
        "\n",
        value,
    )
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location_names(record):
    names = []
    for item in record.get("locations") or []:
        name = item.get("name")
        if name:
            names.append(name)
    return names


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

            detail = json.loads(ctx.fetch(_DETAIL_ROOT + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            if not description:
                continue

            posting_url = f"{_API_ROOT}/apply/{_COMPANY_SLUG}/{job_id}/"
            locations = _location_names(detail) or _location_names(listed)
            location = detail.get("location")
            if not location and locations:
                location = locations[0]

            data = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
            }
            if locations:
                data["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or listed.get("workplace_type")
            )
            if workplace_type:
                data["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                data["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                data["posted_at"] = created

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            currency = compensation.get("currency_code")
            period = compensation.get("salary_range_type")
            if lower is not None:
                data["salary_min"] = lower
            if upper is not None:
                data["salary_max"] = upper
            if (lower is not None or upper is not None) and currency:
                data["salary_currency"] = currency
            if (lower is not None or upper is not None) and period:
                data["salary_period"] = period

            yield Job(**data)

        page_url = page.get("next")
