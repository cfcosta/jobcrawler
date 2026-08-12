import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_ORIGIN = "https://app.dover.com"
_COMPANY = "DeepSource"
_COMPANY_SLUG = "DeepSource"


def _plain_text(value):
    text = html.unescape(value)
    text = re.sub(
        r"(?is)<(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/section|/article|/tr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
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

        for summary in page.get("results") or []:
            if not summary.get("is_published") or summary.get("is_sample"):
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            detail_url = (
                _ORIGIN + "/api/v1/inbound/application-portal-job/" + str(job_id)
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                continue
            description = _plain_text(description_html)
            if not description:
                continue

            location_items = detail.get("locations") or summary.get("locations") or []
            structured_locations = []
            for item in location_items:
                name = item.get("name")
                if name:
                    structured_locations.append(name)

            primary_location = detail.get("location")
            if not primary_location and structured_locations:
                primary_location = structured_locations[0]
            if not primary_location:
                continue

            posting_url = (
                _ORIGIN + "/apply/" + _COMPANY_SLUG + "/" + str(job_id) + "/"
            )
            compensation = detail.get("compensation") or {}
            fields = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": detail.get("title") or summary.get("title"),
                "company": detail.get("client_name") or _COMPANY,
                "location": primary_location,
                "locations": structured_locations or None,
                "description": description,
                "workplace_type": (
                    detail.get("workplace_type") or summary.get("workplace_type")
                ),
                "employment_type": compensation.get("employment_type"),
                "posted_at": detail.get("created"),
                "external_id": str(job_id),
                "source": SITE,
            }

            if (
                compensation.get("open_to_sharing_comp")
                and compensation.get("lower_bound") is not None
                and compensation.get("upper_bound") is not None
            ):
                fields["salary_min"] = compensation.get("lower_bound")
                fields["salary_max"] = compensation.get("upper_bound")
                fields["salary_currency"] = compensation.get("currency_code")
                fields["salary_period"] = compensation.get("salary_range_type")

            yield Job(**{key: value for key, value in fields.items() if value is not None})

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
