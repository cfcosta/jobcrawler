
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_COMPANY = "Caldera"
_CLIENT_SLUG = "caldera"
_LIST_PREFIX = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs"
_DETAIL_PREFIX = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _html_to_text(value):
    value = value or ""
    value = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _json(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "json"):
        return response.json()
    if hasattr(response, "text"):
        value = response.text
        if callable(value):
            value = value()
    else:
        value = response
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _location_names(detail, summary):
    records = detail.get("locations") or summary.get("locations") or []
    names = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        if not name:
            option = record.get("location_option") or {}
            name = option.get("display_name")
        if name and name not in names:
            names.append(name)
    return names


def crawl(ctx):
    offset = 0
    limit = 50
    seen = set()

    while True:
        page_url = f"{_LIST_PREFIX}?limit={limit}&offset={offset}"
        page = _json(ctx, page_url)
        results = page.get("results") or []

        for summary in results:
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen
                or summary.get("is_published") is not True
                or summary.get("is_sample") is True
            ):
                continue
            seen.add(job_id)

            detail = _json(ctx, f"{_DETAIL_PREFIX}{job_id}")
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            title = detail.get("title") or summary.get("title")
            description = _html_to_text(detail.get("user_provided_description"))
            locations = _location_names(detail, summary)
            primary_location = detail.get("location") or (
                locations[0] if locations else None
            )
            if not title or not description or not primary_location:
                continue

            posting_url = f"https://app.dover.com/apply/{_CLIENT_SLUG}/{job_id}/"
            kwargs = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": title,
                "company": _COMPANY,
                "location": primary_location,
                "locations": locations,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            workplace_type = (
                detail.get("workplace_type") or summary.get("workplace_type")
            )
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                kwargs["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type

            salary_min = compensation.get("lower_bound")
            salary_max = compensation.get("upper_bound")
            if salary_min is not None or salary_max is not None:
                if salary_min is not None:
                    kwargs["salary_min"] = salary_min
                if salary_max is not None:
                    kwargs["salary_max"] = salary_max
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if currency:
                    kwargs["salary_currency"] = currency
                if period:
                    kwargs["salary_period"] = period

            compensation_details = detail.get("compensation_details")
            if compensation_details:
                kwargs["salary"] = compensation_details

            yield Job(**kwargs)

        offset += len(results)
        count = page.get("count")
        if not results or page.get("next") is None or (
            isinstance(count, int) and offset >= count
        ):
            break
