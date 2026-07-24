import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobcrawler.runtime.contract import Job


SITE = "https://netapp.eightfold.ai/api/apply/v2/jobs?domain=netapp.com&query=instaclustr&start=0&num=100"
EXTRA_HOSTS = []


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/div|/h[1-6]|/li|/ol|/p|/section|/table|/td|/th|/tr|/ul)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _page_url(start):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["start"] = str(start)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def crawl(ctx):
    positions = []
    seen = set()
    start = 0
    total = None

    while total is None or start < total:
        payload = json.loads(ctx.fetch(_page_url(start)))
        page = payload.get("positions") or []
        count = payload.get("count")
        if count is not None:
            total = count
        if not page:
            break

        for position in page:
            job_id = position.get("id")
            if job_id is not None and job_id not in seen:
                seen.add(job_id)
                positions.append(position)

        start += len(page)

    def fetch_detail(position):
        job_id = position.get("id")
        detail_url = (
            "https://netapp.eightfold.ai/api/apply/v2/jobs/"
            + str(job_id)
            + "?domain=netapp.com&domainType=external"
        )
        return json.loads(ctx.fetch(detail_url))

    positions = positions[:10]
    details = [fetch_detail(position) for position in positions]

    for position, detail in zip(positions, details):
        job_id = position.get("id")
        description = _text(detail.get("job_description"))
        title = detail.get("posting_name") or detail.get("name")
        location = detail.get("location") or position.get("location")
        locations = detail.get("locations") or position.get("locations")
        canonical_url = (
            detail.get("canonicalPositionUrl")
            or position.get("canonicalPositionUrl")
            or "https://netapp.eightfold.ai/careers/job/" + str(job_id)
        )

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Instaclustr",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": locations,
            "department": detail.get("department") or position.get("department"),
            "workplace_type": (
                detail.get("work_location_option")
                or position.get("work_location_option")
            ),
            "posted_at": _posted_at(
                detail.get("t_create")
                if detail.get("t_create") is not None
                else position.get("t_create")
            ),
            "external_id": (
                detail.get("ats_job_id")
                or position.get("ats_job_id")
                or str(job_id)
            ),
            "apply_url": detail.get("apply_redirect_url"),
        }
        fields.update(
            (key, value) for key, value in optional.items()
            if value is not None and value != ""
        )
        yield Job(**fields)
