
from __future__ import annotations

import html
import json
import re
from urllib.parse import quote

SITE = "https://apply.workable.com/api/v1/widget/accounts/strapi"
EXTRA_HOSTS = []

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BREAK_RE = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/div|/li|/h[1-6])\s*>")


def _response_text(response):
    if isinstance(response, str):
        return response
    if isinstance(response, bytes):
        return response.decode("utf-8", "replace")
    if hasattr(response, "text"):
        value = response.text
        return value() if callable(value) else value
    return str(response)


def _get_json(ctx, url):
    return json.loads(_response_text(ctx.fetch(url)))


def _text(value):
    if not value:
        return None
    value = _BREAK_RE.sub("\n", str(value))
    value = _TAG_RE.sub("", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return value or None


def _location(job):
    parts = [job.get("city"), job.get("state"), job.get("country")]
    return ", ".join(str(part).strip() for part in parts if part) or None


def _locations(job):
    result = []
    for location in job.get("locations") or []:
        if isinstance(location, str):
            display = location.strip()
        else:
            parts = [location.get("city"), location.get("state"), location.get("country")]
            display = ", ".join(str(part).strip() for part in parts if part)
        if display:
            result.append(display)
    return result or None


def crawl(ctx):
    board = _get_json(ctx, SITE + "?details=true")
    company = "Strapi"

    for item in board.get("jobs") or []:
        shortcode = item.get("shortcode")
        if not shortcode or not item.get("title"):
            continue

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/strapi/jobs/"
            + quote(str(shortcode), safe="")
        )
        detail = _get_json(ctx, detail_url)
        sections = []
        for field in ("description", "requirements", "benefits"):
            section = _text(detail.get(field))
            if section:
                sections.append(section)
        description = "\n\n".join(sections).strip()
        if not description:
            continue

        posting_url = "https://apply.workable.com/strapi/j/" + quote(str(shortcode), safe="")
        locations = _locations(item)
        primary_location = _location(item)
        if not primary_location and locations:
            primary_location = locations[0]
        if not primary_location:
            continue

        values = {
            "url": posting_url,
            "title": item["title"],
            "company": company,
            "location": primary_location,
            "description": description,
            "source": "Workable",
            "external_id": str(shortcode),
            "apply_url": posting_url,
        }

        if locations:
            values["locations"] = locations
        if item.get("department"):
            values["department"] = item["department"]
        if item.get("employment_type"):
            values["employment_type"] = item["employment_type"]
        if item.get("telecommuting") is True:
            values["workplace_type"] = "remote"

        yield Job(**values)
