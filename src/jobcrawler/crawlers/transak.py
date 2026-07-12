import html
import json
import re


SITE = "https://transak-inc.breezy.hr/json"
EXTRA_HOSTS = []


def plain_text(value):
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def named_value(value):
    if isinstance(value, dict):
        return value.get("name")
    return value


def find_job_posting(value):
    if isinstance(value, dict):
        types = value.get("@type")
        if types == "JobPosting" or (
            isinstance(types, list) and "JobPosting" in types
        ):
            return value
        for child in value.values():
            result = find_job_posting(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_job_posting(child)
            if result:
                return result
    return None


def extract_job_posting(source):
    scripts = re.findall(
        r"<script\b([^>]*)>(.*?)</script>", source, flags=re.I | re.S
    )
    for attributes, script in scripts:
        if not re.search(
            r"\btype\s*=\s*[\"']application/ld\+json[\"']",
            attributes,
            flags=re.I,
        ):
            continue
        try:
            data = json.loads(script.strip())
        except Exception:
            try:
                data = json.loads(html.unescape(script.strip()))
            except Exception:
                continue
        result = find_job_posting(data)
        if result:
            return result
    return {}


def crawl(ctx):
    listing_text = ctx.fetch(SITE)
    listings = json.loads(listing_text)

    for item in listings:
        url = item.get("url")
        if not isinstance(url, str):
            continue
        if not re.match(r"^https://transak-inc\.breezy\.hr(?:/|$)", url):
            continue

        posting = extract_job_posting(ctx.fetch(url))
        location_data = item.get("location") or {}
        location = named_value(location_data)

        locations = []
        raw_locations = item.get("locations")
        if isinstance(raw_locations, list):
            for raw_location in raw_locations:
                name = named_value(raw_location)
                if name:
                    locations.append(name)
        if not locations and location:
            locations = [location]

        fields = {
            "source": SITE,
            "url": url,
            "title": item.get("title")
            or item.get("position")
            or item.get("name")
            or posting.get("title"),
            "company": "Transak",
            "location": location,
            "locations": locations,
            "description": plain_text(posting.get("description", "")),
            "department": named_value(item.get("department")),
            "employment_type": named_value(item.get("type")),
            "workplace_type": (
                "remote"
                if isinstance(location_data, dict)
                and location_data.get("is_remote") is True
                else "onsite"
                if isinstance(location_data, dict)
                and location_data.get("is_remote") is False
                else None
            ),
            "posted_at": item.get("published_date"),
            "external_id": item.get("id"),
            "apply_url": url + "/apply",
        }
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != "" and value != []
        }
        yield fields