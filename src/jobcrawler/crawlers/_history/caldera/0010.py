
import html
import json
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from jobcrawler.runtime.contract import Job

SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

COMPANY = "Caldera"
APPLY_BASE = "https://app.dover.com/apply/caldera/"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"
PAGE_SIZE = 50


def _get_json(ctx, url):
    return json.loads(ctx.fetch(url))


def _page_url(offset):
    parts = urlsplit(SITE)
    query = urlencode({"limit": PAGE_SIZE, "offset": offset})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _plain_text(value):
    if not value:
        return None
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|"
        r"nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    offset = 0
    seen = set()

    while True:
        payload = _get_json(ctx, _page_url(offset))
        results = payload.get("results") or []

        for listed in results:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen
                or listed.get("is_published") is not True
                or listed.get("is_sample") is True
            ):
                continue
            seen.add(job_id)

            detail = _get_json(ctx, DETAIL_BASE + job_id)
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            title = detail.get("title") or listed.get("title")
            description = _plain_text(detail.get("user_provided_description"))
            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            primary_location = detail.get("location") or (
                locations[0] if locations else None
            )
            if not title or not description or not primary_location:
                continue

            apply_url = APPLY_BASE + job_id + "/"
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": title,
                "company": COMPANY,
                "location": primary_location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
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

            compensation_text = detail.get("compensation_details")
            if isinstance(compensation_text, str) and compensation_text.strip():
                fields["salary"] = compensation_text.strip()

            if detail.get("created"):
                fields["posted_at"] = detail["created"]

            yield Job(**fields)

        count = payload.get("count")
        offset += len(results)
        if not results or (isinstance(count, int) and offset >= count):
            break
        if len(results) < PAGE_SIZE and count is None:
            break
