
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_BOARD_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_COMPANY = "Caldera"
_COMPANY_ROUTE = "caldera"
_PAGE_SIZE = 50


def _plain_text(value):
    if not value:
        return None

    text = unescape(value)
    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|"
        r"h[1-6]|header|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)"
        r"\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]*>", " ", text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    offset = 0

    while True:
        if offset == 0:
            page_url = SITE
        else:
            page_url = (
                "https://app.dover.com/api/v1/careers-page/"
                + _BOARD_ID
                + "/jobs?limit="
                + str(_PAGE_SIZE)
                + "&offset="
                + str(offset)
            )

        page = json.loads(ctx.fetch(page_url))
        results = page.get("results") or []

        for listed in results:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/job-board/jobs/"
                + job_id
                + "/"
            )
            detail = json.loads(ctx.fetch(detail_url))

            description = _plain_text(
                detail.get("user_provided_description")
            )
            if not description:
                continue

            source_locations = (
                detail.get("locations")
                or listed.get("locations")
                or []
            )
            locations = [
                item.get("name")
                for item in source_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = locations[0] if locations else None

            posting_url = (
                "https://app.dover.com/apply/"
                + _COMPANY_ROUTE
                + "/"
                + job_id
                + "/"
            )
            fields = {
                "url": posting_url,
                "title": detail.get("title") or listed.get("title"),
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": posting_url,
                "employment_type": detail.get("employment_type"),
                "workplace_type": (
                    detail.get("workplace_type")
                    or listed.get("workplace_type")
                ),
                "posted_at": detail.get("date_posted"),
            }

            if locations:
                fields["locations"] = locations

            compensation = detail.get("compensation") or {}
            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            currency = compensation.get("currency_code")
            period = compensation.get("salary_range_type")
            if (
                lower is not None
                and upper is not None
                and currency
                and period
            ):
                fields["salary_min"] = lower
                fields["salary_max"] = upper
                fields["salary_currency"] = currency
                fields["salary_period"] = period

            yield Job(
                **{
                    key: value
                    for key, value in fields.items()
                    if value is not None
                }
            )

        offset += len(results)
        count = page.get("count")
        if not results or page.get("next") is None:
            break
        if isinstance(count, int) and offset >= count:
            break
