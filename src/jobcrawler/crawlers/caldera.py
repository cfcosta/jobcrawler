import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from jobcrawler.runtime.contract import Job


SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_COMPANY = "Caldera"
_APPLY_SLUG = "caldera"


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</?\s*(?:p|div|h[1-6]|ul|ol|section|article|"
        r"blockquote|pre|table|tr|dl|dt|dd)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _json(ctx, url):
    return json.loads(ctx.fetch(url))


def _page_url(offset, limit):
    parts = urlsplit(SITE)
    query = urlencode({"limit": limit, "offset": offset})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def crawl(ctx):
    page_url = _page_url(0, 50)
    seen = set()

    while page_url:
        page = _json(ctx, page_url)
        rows = page.get("results") or []

        for row in rows:
            job_id = row.get("id")
            if (
                not job_id
                or job_id in seen
                or not row.get("is_published")
                or row.get("is_sample")
            ):
                continue
            seen.add(job_id)

            detail_url = (
                "https://app.dover.com/api/v1/job-board/jobs/"
                + str(job_id)
                + "/"
            )
            detail = _json(ctx, detail_url)
            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or row.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            location = locations[0] if locations else None

            apply_url = (
                "https://app.dover.com/apply/"
                + _APPLY_SLUG
                + "/"
                + str(job_id)
                + "/"
            )
            compensation = detail.get("compensation") or {}
            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")

            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": detail.get("title") or row.get("title"),
                "company": _COMPANY,
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "workplace_type": (
                    detail.get("workplace_type") or row.get("workplace_type")
                ),
                "employment_type": detail.get("employment_type"),
                "posted_at": detail.get("date_posted"),
            }
            if locations:
                fields["locations"] = locations
            if lower is not None:
                fields["salary_min"] = lower
            if upper is not None:
                fields["salary_max"] = upper
            if (lower is not None or upper is not None) and compensation.get("currency_code"):
                fields["salary_currency"] = compensation["currency_code"]
            if (lower is not None or upper is not None) and compensation.get("salary_range_type"):
                fields["salary_period"] = compensation["salary_range_type"]

            yield Job(**{
                key: value
                for key, value in fields.items()
                if value is not None
            })

        page_url = page.get("next")
