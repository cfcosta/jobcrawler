import html
import json
import re
from urllib.parse import urlencode

from jobcrawler.runtime.contract import Job


SITE = 'https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0'
EXTRA_HOSTS = []

_ORG_ID = "beead238-de58-4e47-8bc4-b3b45b1f55d6"
_BOARD_SLUG = "caldera"
_API_ROOT = "https://app.dover.com/api/v1"
_APPLY_ROOT = "https://app.dover.com/apply/caldera"
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "div", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "ol",
    "p", "pre", "section", "table", "td", "th", "tr", "ul",
}


def _plain_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        text,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|"
        r"h[1-6]|header|li|main|ol|p|pre|section|table|td|th|tr|ul)"
        r"\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines) or None


def _json(ctx, url):
    return json.loads(ctx.fetch(url))


def crawl(ctx):
    offset = 0
    limit = 50

    while True:
        list_url = (
            f"{_API_ROOT}/careers-page/{_ORG_ID}/jobs?"
            + urlencode({"limit": limit, "offset": offset})
        )
        page = _json(ctx, list_url)
        rows = page.get("results") or []

        for row in rows:
            if not row.get("is_published") or row.get("is_sample"):
                continue

            job_id = row.get("id")
            if not job_id:
                continue

            detail_url = (
                f"{_API_ROOT}/inbound/application-portal-job/{job_id}"
                f"?rs={_BOARD_SLUG}"
            )
            detail = _json(ctx, detail_url)
            if not detail.get("active", True) or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or row.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or row.get("locations") or []
            locations = [
                item["name"].strip()
                for item in raw_locations
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ]
            primary = next(
                (
                    item.get("name", "").strip()
                    for item in raw_locations
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and isinstance(item.get("name"), str)
                    and item.get("name", "").strip()
                ),
                None,
            )
            location = detail.get("location") or primary or (
                locations[0] if locations else None
            )
            if not location:
                continue

            posting_url = f"{_APPLY_ROOT}/{job_id}/"
            compensation = detail.get("compensation") or {}
            kwargs = {
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
                kwargs["locations"] = locations
            if detail.get("workplace_type"):
                kwargs["workplace_type"] = detail["workplace_type"]
            if detail.get("created"):
                kwargs["posted_at"] = detail["created"]
            if compensation.get("employment_type"):
                kwargs["employment_type"] = compensation["employment_type"]
            if detail.get("compensation_details"):
                kwargs["salary"] = detail["compensation_details"]

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                if lower is not None:
                    kwargs["salary_min"] = lower
                if upper is not None:
                    kwargs["salary_max"] = upper
                if compensation.get("currency_code"):
                    kwargs["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    kwargs["salary_period"] = compensation["salary_range_type"]

            yield Job(**kwargs)

        offset += len(rows)
        count = page.get("count")
        if not rows or (isinstance(count, int) and offset >= count):
            break
