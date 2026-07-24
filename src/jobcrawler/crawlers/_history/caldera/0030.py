
import html
import json
import re

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

_ORIGIN = "https://app.dover.com"
_BLOCK_TAGS = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
    r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
    r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_HIDDEN = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)


def _plain_text(value):
    value = value or ""
    value = _HIDDEN.sub("", value)
    value = _BLOCK_TAGS.sub("\n", value)
    value = _TAGS.sub("", value)
    value = html.unescape(value)

    lines = []
    for raw_line in value.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _same_host_url(value):
    if not value:
        return None
    if value.startswith(_ORIGIN + "/"):
        return value
    if value.startswith("/"):
        return _ORIGIN + value
    return None


def crawl(ctx):
    page_url = SITE
    seen_pages = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = json.loads(ctx.fetch(page_url))

        for summary in page.get("results") or []:
            if not summary.get("is_published") or summary.get("is_sample"):
                continue

            job_id = summary.get("id")
            if not job_id:
                continue

            detail_url = (
                f"{_ORIGIN}/api/v1/inbound/application-portal-job/{job_id}"
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue

            title = detail.get("title") or summary.get("title")
            description = _plain_text(
                detail.get("user_provided_description")
            )
            if not title or not description:
                continue

            apply_url = f"{_ORIGIN}/apply/caldera/{job_id}/"

            raw_locations = (
                detail.get("locations")
                or summary.get("locations")
                or []
            )
            locations = []
            for item in raw_locations:
                name = item.get("name")
                if not name:
                    name = (item.get("location_option") or {}).get(
                        "display_name"
                    )
                if name:
                    locations.append(name)

            location = detail.get("location")
            if not location and locations:
                location = locations[0]
            if not location:
                continue

            compensation = detail.get("compensation") or {}
            fields = {
                "url": apply_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": str(job_id),
                "apply_url": apply_url,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = (
                detail.get("workplace_type")
                or summary.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            if detail.get("created"):
                fields["posted_at"] = detail["created"]

            yield Job(**fields)

        page_url = _same_host_url(page.get("next"))
