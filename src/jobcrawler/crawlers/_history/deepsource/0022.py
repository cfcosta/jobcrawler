import json
import re
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _html_to_text(value):
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|blockquote|pre)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _page_url(offset):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["offset"] = str(offset)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
    )


def crawl(ctx):
    offset = 0
    seen = set()

    while True:
        payload = json.loads(ctx.fetch(_page_url(offset)))
        rows = payload.get("results") or []

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
                "https://app.dover.com/api/v1/jobs/"
                + job_id
                + "/get_job_description"
            )
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("is_published"):
                continue

            description_html = detail.get("user_provided_description")
            if description_html:
                description = _html_to_text(description_html)
            else:
                description = (
                    detail.get("user_facing_description") or ""
                ).strip()
            if not description:
                continue

            location_names = [
                item["name"]
                for item in (row.get("locations") or [])
                if item.get("name")
            ]
            location = location_names[0] if location_names else "Unspecified"
            posting_url = (
                "https://app.dover.com/apply/DeepSource/" + job_id + "/"
            )

            fields = {
                "url": posting_url,
                "apply_url": posting_url,
                "title": row.get("title"),
                "company": "DeepSource",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
            }
            if location_names:
                fields["locations"] = location_names
            if row.get("workplace_type"):
                fields["workplace_type"] = row["workplace_type"]

            yield Job(**fields)

        if not payload.get("next") or not rows:
            break
        offset += len(rows)
