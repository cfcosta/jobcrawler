import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = 'https://apply.workable.com/api/v1/widget/accounts/seldon?details=true'
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    text = unescape(value)
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", text)
    text = re.sub(
        r"(?i)<(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text).replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _location(location):
    if isinstance(location, str):
        return location.strip() or None
    if not isinstance(location, dict):
        return None
    parts = []
    for key in ("city", "region", "country"):
        value = location.get(key)
        if value and value not in parts:
            parts.append(value)
    if parts:
        return ", ".join(parts)
    if location.get("telecommuting"):
        return "Remote"
    return None


def _put(values, key, value):
    if value is not None and value != "":
        values[key] = value


def crawl(ctx):
    response = ctx.fetch(SITE)
    data = json.loads(response)
    company = data.get("name") or "Seldon Technologies"

    for item in data.get("jobs", []):
        if item.get("state") != "published":
            continue

        shortcode = item.get("shortcode")
        title = item.get("title")
        if not shortcode or not title:
            continue

        sections = []
        for key in ("description", "requirements", "benefits"):
            section = _text(item.get(key))
            if section:
                sections.append(section)
        description = "\n\n".join(sections)
        if not description:
            continue

        primary_location = _location(item.get("location"))
        if not primary_location:
            continue

        values = {
            "url": "https://apply.workable.com/seldon/j/" + shortcode + "/",
            "title": title,
            "company": company,
            "location": primary_location,
            "description": description,
            "source": SITE,
            "external_id": shortcode,
        }
        _put(values, "department", item.get("department"))
        _put(values, "employment_type", item.get("employment_type"))
        _put(values, "apply_url", item.get("application_url"))

        location_data = item.get("location")
        if isinstance(location_data, dict) and location_data.get("telecommuting"):
            values["workplace_type"] = "remote"

        structured_locations = item.get("locations")
        if isinstance(structured_locations, list):
            locations = []
            for entry in structured_locations:
                value = _location(entry)
                if value:
                    locations.append(value)
            if locations:
                values["locations"] = locations

        yield Job(**values)
