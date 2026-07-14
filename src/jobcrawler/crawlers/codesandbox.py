
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/codesandbox"
EXTRA_HOSTS = []


def clean_html(value):
    if not value:
        return ""
    text = str(value)
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(
        r"(?i)</?(?:p|div|li|ul|ol|h[1-6]|section|article)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def location_name(value):
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    for key in ("name", "location", "location_name"):
        if value.get(key):
            return str(value.get(key)).strip()

    parts = []
    for key in ("city", "state", "region", "country"):
        part = value.get(key)
        if part and str(part).strip() not in parts:
            parts.append(str(part).strip())
    return ", ".join(parts) or None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    for item in board.get("jobs") or []:
        shortcode = item.get("shortcode")
        if not shortcode:
            continue

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/codesandbox/jobs/"
            + str(shortcode)
        )
        detail = json.loads(ctx.fetch(detail_url))
        if detail.get("state") not in (None, "published"):
            continue

        sections = []
        for key in ("description", "requirements", "benefits"):
            section = clean_html(detail.get(key))
            if section:
                sections.append(section)
        description = "\n\n".join(sections)
        if not description:
            continue

        raw_locations = item.get("locations")
        if raw_locations is None:
            raw_locations = detail.get("locations") or []

        locations = []
        for value in raw_locations:
            name = location_name(value)
            if name and name not in locations:
                locations.append(name)

        location = location_name(item.get("location"))
        if not location:
            location = location_name(detail.get("location"))
        if not location and locations:
            location = locations[0]
        if not location:
            location = location_name(item) or location_name(detail)

        url = detail.get("url") or item.get("url")
        fields = {
            "url": url,
            "title": detail.get("title") or item.get("title"),
            "company": "CodeSandbox",
            "location": location,
            "description": description,
            "source": "Workable",
            "external_id": str(shortcode),
            "apply_url": (
                detail.get("application_url")
                or detail.get("apply_url")
                or item.get("application_url")
                or url
            ),
            "department": detail.get("department") or item.get("department"),
            "employment_type": (
                detail.get("employment_type") or item.get("employment_type")
            ),
            "locations": locations or None,
            "posted_at": (
                detail.get("published_on")
                or detail.get("published_at")
                or item.get("published_on")
                or item.get("published_at")
            ),
            "expires_at": detail.get("expires_at") or item.get("expires_at"),
        }

        if detail.get("telecommuting") or item.get("telecommuting"):
            fields["workplace_type"] = "remote"

        output = {}
        for key in fields:
            value = fields[key]
            if value is not None and value != "":
                output[key] = value
        yield Job(**output)
