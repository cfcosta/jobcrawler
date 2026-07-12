
import html
import json
import re

SITE = "https://zero-hash.breezy.hr/json"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    value = re.sub(
        r"<(?:br|/p|/div|/li|/h[1-6])\s*/?>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<li(?:\s[^>]*)?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _jobposting(page):
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )
    for raw in blocks:
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue

        pending = value if isinstance(value, list) else [value]
        while pending:
            obj = pending.pop(0)
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") == "JobPosting":
                return obj
            graph = obj.get("@graph")
            if isinstance(graph, list):
                pending.extend(graph)
    return {}


def _fallback_description(page):
    match = re.search(
        r'<div[^>]+class=["\'][^"\']*\bdescription\b[^"\']*["\'][^>]*>'
        r'(.*?)'
        r'</div>\s*<div[^>]+class=["\'](?:apply|application|position|footer)',
        page,
        flags=re.I | re.S,
    )
    if match:
        return _clean_html(match.group(1))

    match = re.search(
        r'<div[^>]+class=["\'][^"\']*\bdescription\b[^"\']*["\'][^>]*>'
        r'(.*?)'
        r'<form\b',
        page,
        flags=re.I | re.S,
    )
    return _clean_html(match.group(1)) if match else None


def _location_names(record):
    names = []
    for location in record.get("locations") or []:
        if isinstance(location, dict):
            name = location.get("name")
            if name and name not in names:
                names.append(name)
    return names or None


def crawl(ctx):
    records = json.loads(ctx.fetch(SITE))[:16]

    for record in records:
        url = record.get("url")
        title = record.get("name")
        if not url or not title:
            continue

        page = ctx.fetch(url)
        structured = _jobposting(page)
        description = _clean_html(structured.get("description"))
        if not description:
            description = _fallback_description(page)
        if not description:
            continue

        location_obj = record.get("location") or {}
        location = location_obj.get("name")
        if not location:
            job_location = structured.get("jobLocation") or {}
            if isinstance(job_location, list):
                job_location = job_location[0] if job_location else {}
            address = job_location.get("address") or {}
            location = ", ".join(
                str(address[key])
                for key in ("addressLocality", "addressRegion", "addressCountry")
                if address.get(key)
            )
        if not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "zerohash",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": record.get("id"),
            "posted_at": record.get("published_date"),
            "employment_type": (record.get("type") or {}).get("name"),
            "department": record.get("department"),
            "locations": _location_names(record),
        }

        if location_obj.get("is_remote") is True:
            fields["workplace_type"] = "remote"

        salary = record.get("salary")
        if isinstance(salary, str) and salary.strip():
            fields["salary"] = salary.strip()

        yield {key: value for key, value in fields.items() if value is not None}
