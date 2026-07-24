import json
import re
from html import unescape
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

COMPANY = "DeepSource"
APPLY_BASE = "https://app.dover.com/apply/DeepSource"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job"


def _json(response):
    try:
        return response.json()
    except AttributeError:
        return json.loads(response)


def _text(value):
    if not value:
        return None
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|"
        r"header|hr|li|main|nav|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"(?s)<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    lines = []
    for line in unescape(text).splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    result = "\n".join(lines)
    return result or None


def _page_url(offset, limit):
    parts = urlsplit(SITE)
    query = parse_qs(parts.query)
    query["limit"] = [str(limit)]
    query["offset"] = [str(offset)]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def crawl(ctx):
    limit = 50
    offset = 0

    while True:
        page = _json(ctx.fetch(_page_url(offset, limit)))
        results = page.get("results") or []

        for listing in results:
            if not listing.get("is_published") or listing.get("is_sample"):
                continue

            job_id = listing.get("id")
            if not job_id:
                continue

            detail = _json(ctx.fetch(f"{DETAIL_BASE}/{job_id}"))
            if not detail.get("active") or detail.get("is_private"):
                continue

            location_records = detail.get("locations") or listing.get("locations") or []
            locations = [
                item.get("name")
                for item in location_records
                if isinstance(item, dict) and item.get("name")
            ]
            primary = next(
                (
                    item.get("name")
                    for item in location_records
                    if isinstance(item, dict)
                    and item.get("is_primary")
                    and item.get("name")
                ),
                None,
            )
            location = detail.get("location") or primary or (
                locations[0] if locations else None
            )
            apply_url = f"{APPLY_BASE}/{job_id}/"
            compensation = detail.get("compensation") or {}

            yield Job(
                url=apply_url,
                title=detail.get("title") or listing.get("title"),
                company=COMPANY,
                location=location,
                locations=locations or None,
                description=_text(detail.get("user_provided_description")),
                source=SITE,
                workplace_type=detail.get("workplace_type")
                or listing.get("workplace_type"),
                employment_type=compensation.get("employment_type"),
                posted_at=detail.get("created"),
                external_id=job_id,
                apply_url=apply_url,
            )

        count = page.get("count")
        offset += len(results)
        if (
            not results
            or page.get("next") is None
            or (isinstance(count, int) and offset >= count)
        ):
            break
