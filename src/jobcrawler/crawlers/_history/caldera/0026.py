import html
import json
import re
from urllib.parse import quote

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_API_ROOT = "https://app.dover.com"
_COMPANY = "Caldera"
_CLIENT_SLUG = "caldera"


def _plain_text(value):
    value = value or ""
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|footer|h[1-6]|"
        r"header|hr|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _json(ctx, url):
    return json.loads(ctx.fetch(url))


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = _json(ctx, page_url)

        for listed in page.get("results") or []:
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail_url = (
                _API_ROOT
                + "/api/v1/inbound/application-portal-job/"
                + quote(str(job_id), safe="")
            )
            detail = _json(ctx, detail_url)
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = detail.get("location") or (locations[0] if locations else None)
            if not location:
                continue

            canonical_url = (
                _API_ROOT
                + "/apply/"
                + _CLIENT_SLUG
                + "/"
                + quote(str(job_id), safe="")
                + "/"
            )
            fields = {
                "url": canonical_url,
                "title": title,
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": canonical_url,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

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

            compensation_details = detail.get("compensation_details")
            if compensation_details:
                fields["salary"] = compensation_details

            yield Job(**fields)

        page_url = page.get("next")
