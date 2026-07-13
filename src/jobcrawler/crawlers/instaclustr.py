import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SITE = "https://netapp.eightfold.ai/api/apply/v2/jobs?domain=netapp.com&query=instaclustr&start=0&num=100"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _page_url(start):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["start"] = str(start)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _iso_timestamp(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _fetch_detail(ctx, position):
    position_id = position.get("id")
    detail_url = (
        "https://netapp.eightfold.ai/api/apply/v2/jobs/"
        + str(position_id)
        + "?domain=netapp.com&domainType=external"
    )
    return position, json.loads(ctx.fetch(detail_url))


def crawl(ctx):
    start = 0
    seen = set()
    positions_to_fetch = []

    while True:
        listing = json.loads(ctx.fetch(_page_url(start)))
        positions = listing.get("positions") or []
        if not positions:
            break

        for position in positions:
            position_id = position.get("id")
            if position_id is None or position_id in seen:
                continue
            seen.add(position_id)
            positions_to_fetch.append(position)

        start += len(positions)
        total = listing.get("count")
        if isinstance(total, int) and start >= total:
            break

    positions_to_fetch = positions_to_fetch[:8]

    for position in positions_to_fetch:
        position, detail = _fetch_detail(ctx, position)
        position_id = position.get("id")
        description = _plain_text(detail.get("job_description") or "")
        title = detail.get("posting_name") or detail.get("name")
        location = detail.get("location")
        canonical_url = detail.get("canonicalPositionUrl")

        if not (title and location and canonical_url and description):
            continue

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Instaclustr",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(
                detail.get("display_job_id")
                or detail.get("ats_job_id")
                or position_id
            ),
        }

        locations = detail.get("locations")
        if isinstance(locations, list) and locations:
            fields["locations"] = locations

        department = detail.get("department")
        if department:
            fields["department"] = department

        workplace_type = (
            detail.get("work_location_option")
            or position.get("work_location_option")
            or detail.get("location_flexibility")
            or position.get("location_flexibility")
        )
        if workplace_type:
            fields["workplace_type"] = workplace_type

        posted_at = _iso_timestamp(detail.get("t_create"))
        if posted_at:
            fields["posted_at"] = posted_at

        apply_url = detail.get("apply_redirect_url")
        if apply_url:
            fields["apply_url"] = apply_url

        yield fields