
import html
import json
import re

SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_BOARD_API = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs"
_DETAIL_API = "https://app.dover.com/api/v1/inbound/application-portal-job/"
_APPLY_BASE = "https://app.dover.com/apply/caldera/"
_PAGE_SIZE = 50


def _text_from_html(value):
    if not value:
        return None

    value = re.sub(
        r"(?i)<br\s*/?>|</(?:p|div|h[1-6]|li|ul|ol|section|article)>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    offset = 0
    seen = set()

    while True:
        if offset == 0:
            page_url = SITE
        else:
            page_url = (
                _BOARD_API
                + "?limit="
                + str(_PAGE_SIZE)
                + "&offset="
                + str(offset)
            )

        page = json.loads(ctx.fetch(page_url))
        results = page.get("results") or []

        for listed in results:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen
                or not listed.get("is_published")
                or listed.get("is_sample")
            ):
                continue
            seen.add(job_id)

            detail = json.loads(ctx.fetch(_DETAIL_API + job_id))
            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _text_from_html(
                detail.get("user_provided_description")
            )
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations")
            if raw_locations is None:
                raw_locations = listed.get("locations") or []

            locations = []
            for place in raw_locations:
                name = place.get("name")
                if name:
                    locations.append(name)

            location = detail.get("location")
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            apply_url = _APPLY_BASE + job_id + "/"
            item = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
            }

            if locations:
                item["locations"] = locations

            workplace_type = (
                detail.get("workplace_type")
                or listed.get("workplace_type")
            )
            if workplace_type:
                item["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                item["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                item["employment_type"] = employment_type

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                if lower is not None:
                    item["salary_min"] = lower
                if upper is not None:
                    item["salary_max"] = upper

                currency = compensation.get("currency_code")
                if currency:
                    item["salary_currency"] = currency

                period = compensation.get("salary_range_type")
                if period:
                    item["salary_period"] = period

                printed_salary = detail.get("compensation_details")
                if printed_salary:
                    item["salary"] = printed_salary

            yield item

        offset += len(results)
        total = page.get("count")
        if not results or len(results) < _PAGE_SIZE:
            break
        if total is not None and offset >= total:
            break
