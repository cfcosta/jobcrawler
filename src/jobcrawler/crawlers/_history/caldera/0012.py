
import json
import re
from html import unescape
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _response_json(response):
    return json.loads(response)


def _description(value):
    html = value or ""
    html = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    html = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    text = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in text.split("\n")
    ]
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = _response_json(ctx.fetch(page_url))

        for listed in page.get("results") or []:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = _response_json(ctx.fetch(detail_url))
            if not detail.get("active", True) or detail.get("is_private"):
                continue

            posting_url = "https://app.dover.com/apply/caldera/" + job_id + "/"
            location_records = (
                detail.get("locations") or listed.get("locations") or []
            )
            locations = [
                item.get("name")
                or (item.get("location_option") or {}).get("display_name")
                for item in location_records
            ]
            locations = [value for value in locations if value]

            primary = next(
                (
                    item.get("name")
                    or (item.get("location_option") or {}).get("display_name")
                    for item in location_records
                    if item.get("is_primary")
                    and (
                        item.get("name")
                        or (item.get("location_option") or {}).get("display_name")
                    )
                ),
                None,
            )
            location = (
                detail.get("location")
                or primary
                or (locations[0] if locations else None)
            )
            title = detail.get("title") or listed.get("title")
            description = _description(detail.get("user_provided_description"))

            if not title or not location or not description:
                continue

            fields = {
                "url": posting_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": posting_url,
            }
            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type") or listed.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if detail.get("created"):
                fields["posted_at"] = detail["created"]

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

            yield Job(**fields)

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
