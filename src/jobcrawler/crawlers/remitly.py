"""Crawler for Remitly's Workday job board (myworkdayjobs.com CXS API)."""

import json
import re
import html

SITE = "https://remitly.wd5.myworkdayjobs.com/wday/cxs/remitly/Remitly_Careers/jobs"
EXTRA_HOSTS = []

COMPANY = "Remitly"
BASE = "https://remitly.wd5.myworkdayjobs.com"
PUBLIC_BASE = "https://remitly.wd5.myworkdayjobs.com/Remitly_Careers"
CXS_BASE = "https://remitly.wd5.myworkdayjobs.com/wday/cxs/remitly/Remitly_Careers"
MAX_JOBS = 50


def _strip_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", text)
    text = re.sub(r"(?is)<\s*(br|p|div|li|tr|h[1-6])\b[^>]*>", "\n", text)
    text = re.sub(r"(?is)<\s*/\s*(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = "\n".join(part.strip() for part in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = html.unescape(value).strip()
        return value or None
    return value


def _post_json(ctx, url, body):
    return json.loads(ctx.fetch(url, method="POST", json=body))


def _get_json(ctx, url):
    return json.loads(ctx.fetch(url))


def _absolute_url(path_or_url):
    if not path_or_url:
        return None
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if path_or_url.startswith("/"):
        return BASE + path_or_url
    return BASE + "/" + path_or_url


def _public_url(external_path):
    if not external_path:
        return None
    if external_path.startswith("http://") or external_path.startswith("https://"):
        return external_path
    if external_path.startswith("/"):
        return PUBLIC_BASE + external_path
    return PUBLIC_BASE + "/" + external_path


def _detail_url(external_path):
    if not external_path:
        return None
    if external_path.startswith("http://") or external_path.startswith("https://"):
        return external_path
    if external_path.startswith("/wday/cxs/"):
        return BASE + external_path
    if external_path.startswith("/"):
        return CXS_BASE + external_path
    return CXS_BASE + "/" + external_path


def _location_name(item):
    if isinstance(item, str):
        return _clean(item)
    if not isinstance(item, dict):
        return None
    for key in ("location", "descriptor", "name", "displayName", "formattedLocation"):
        value = item.get(key)
        if isinstance(value, dict):
            value = _location_name(value)
        value = _clean(value)
        if value:
            return value
    return None


def _locations_from_detail(info):
    locations = []
    for key in ("jobRequisitionLocation", "locations", "additionalLocations"):
        value = info.get(key)
        if isinstance(value, list):
            for item in value:
                name = _location_name(item)
                if name and name not in locations:
                    locations.append(name)
        else:
            name = _location_name(value)
            if name and name not in locations:
                locations.append(name)
    primary = _location_name(info.get("location"))
    if primary and primary not in locations:
        locations.insert(0, primary)
    return locations


def _first(*values):
    for value in values:
        value = _clean(value)
        if value:
            return value
    return None


def _workplace_type(info, row):
    value = _first(info.get("remoteType"), row.get("remoteType"))
    if not value:
        return None
    lowered = value.lower()
    if lowered == "yes" or "remote" in lowered:
        return "Remote"
    if "hybrid" in lowered:
        return "Hybrid"
    if (
        lowered == "no"
        or "onsite" in lowered
        or "on-site" in lowered
        or "office" in lowered
    ):
        return "On-site"
    return value


def _external_id(info, row):
    value = _first(info.get("jobReqId"), info.get("jobRequisitionId"))
    if value:
        return value
    fields = row.get("bulletFields")
    if isinstance(fields, list):
        for field in fields:
            field = _clean(field)
            if field:
                return field
    return None


def _job_from_row(ctx, row):
    external_path = row.get("externalPath")
    detail_url = _detail_url(external_path)
    if not detail_url:
        return None

    detail = _get_json(ctx, detail_url)
    info = detail.get("jobPostingInfo") or detail.get("jobPosting") or detail

    title = _first(info.get("title"), row.get("title"))
    description = _strip_html(info.get("jobDescription") or info.get("description"))
    if not title or not description:
        return None

    locations = _locations_from_detail(info)
    location = _first(_location_name(info.get("location")), row.get("locationsText"))
    if not location and locations:
        location = locations[0]
    if not location:
        return None

    apply_url = _first(info.get("externalUrl"), info.get("applyUrl"), info.get("url"))
    job = {
        "url": _public_url(external_path) or detail_url,
        "title": title,
        "company": COMPANY,
        "location": location,
        "description": description,
        "source": SITE,
    }

    if locations:
        job["locations"] = locations

    optional = {
        "external_id": _external_id(info, row),
        "apply_url": _absolute_url(apply_url),
        "employment_type": _first(
            info.get("timeType"), info.get("workerType"), row.get("timeType")
        ),
        "workplace_type": _workplace_type(info, row),
    }
    start_date = _clean(info.get("startDate"))
    if start_date:
        optional["posted_at"] = start_date

    for key, value in optional.items():
        if value:
            job[key] = value

    return job


def crawl(ctx):
    limit = 20
    offset = 0
    total = None
    seen = set()
    yielded = 0

    while total is None or offset < total:
        body = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
        }
        data = _post_json(ctx, SITE, body)
        total = data.get("total")
        rows = data.get("jobPostings") or data.get("jobs") or data.get("postings") or []

        if not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("externalPath") or row.get("title")
            if key in seen:
                continue
            seen.add(key)
            job = _job_from_row(ctx, row)
            if job:
                yield job
                yielded += 1
                if yielded >= MAX_JOBS:
                    return

        offset += limit
        if total is None and len(rows) < limit:
            break
