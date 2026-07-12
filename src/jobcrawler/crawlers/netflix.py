
import html
import json
import re
from urllib.parse import urlencode


SITE = "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&start=0&num=10&sort_by=relevance"
EXTRA_HOSTS = []


def _text(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*br\s*/?\s*>|</\s*(?:p|li|div|h[1-6]|tr|section|ul|ol)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _json(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "json"):
        return response.json()
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    return json.loads(response)


def crawl(ctx):
    start = 0
    seen = set()
    selected = []
    total = None

    while (total is None or start < total) and len(selected) < 3:
        query = urlencode({
            "domain": "netflix.com",
            "start": start,
            "num": 10,
            "sort_by": "relevance",
            "Teams": "Engineering",
        })
        page = _json(
            ctx,
            "https://explore.jobs.netflix.net/api/apply/v2/jobs?" + query,
        )
        positions = page.get("positions") or []
        total = page.get("count", total)

        for listed in positions:
            if listed.get("department") != "Engineering":
                continue

            job_id = listed.get("id")
            if job_id is None or job_id in seen:
                continue
            seen.add(job_id)

            role_text = (listed.get("name") or "").lower()
            relevant = (
                "backend" in role_text
                or "platform" in role_text
                or "data" in role_text
                or "security" in role_text
                or "infrastructure" in role_text
                or "distributed systems" in role_text
                or "software engineer" in role_text
            )
            if not relevant or len(selected) >= 3:
                continue
            selected.append(listed)

        if not positions:
            break
        start += 10

    for listed in selected:
        job_id = listed.get("id")
        detail = _json(
                ctx,
                "https://explore.jobs.netflix.net/api/apply/v2/jobs/"
                + str(job_id)
                + "?domain=netflix.com",
            )
        description = _text(detail.get("job_description"))
        title = detail.get("name") or listed.get("name")
        location = detail.get("location") or listed.get("location")
        canonical_url = (
            detail.get("canonicalPositionUrl")
            or listed.get("canonicalPositionUrl")
        )

        if not title or not location or not canonical_url or not description:
            continue

        locations = detail.get("locations") or listed.get("locations")
        department = detail.get("department") or listed.get("department")
        workplace_type = (
            detail.get("work_location_option")
            or listed.get("work_location_option")
        )

        custom_fields = (
            (detail.get("custom_JD") or {}).get("data_fields") or {}
        )
        if not workplace_type:
            work_types = custom_fields.get("work_type") or []
            if work_types:
                workplace_type = work_types[0]

        external_id = (
            detail.get("display_job_id")
            or detail.get("ats_job_id")
            or listed.get("display_job_id")
            or listed.get("ats_job_id")
            or str(job_id)
        )

        fields = {
            "url": canonical_url,
            "title": title,
            "company": "Netflix",
            "location": location,
            "description": description,
            "external_id": external_id,
            "apply_url": canonical_url,
            "department": department,
            "workplace_type": workplace_type,
        }
        if locations:
            fields["locations"] = locations

        fields["source"] = SITE
        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
