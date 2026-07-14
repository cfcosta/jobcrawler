
import json
import re
from html import unescape


SITE = "https://apply.workable.com/api/v1/widget/accounts/conduktor"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None

    text = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
    )
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location(value):
    if not isinstance(value, dict):
        text = str(value).strip() if value is not None else ""
        return text or None

    parts = []
    for key in ("city", "region", "country"):
        part = value.get(key)
        if part:
            part = str(part).strip()
            if part and part not in parts:
                parts.append(part)
    return ", ".join(parts) or None


def _json(response):
    if isinstance(response, str):
        text = response
    else:
        try:
            return response.json()
        except AttributeError:
            text = response.text

    match = re.search(r"(?is)<pre[^>]*>(.*?)</pre>", text)
    if match:
        text = unescape(match.group(1))
    return json.loads(text)


def crawl(ctx):
    response = ctx.fetch(SITE + "?details=true")
    board = _json(response)
    seen = set()

    for listed in board.get("jobs") or []:
        shortcode = listed.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/conduktor/jobs/"
            + str(shortcode)
        )
        detail = _json(ctx.fetch(detail_url))
        if detail.get("state") != "published":
            continue

        sections = [
            _text(detail.get(name))
            for name in ("description", "requirements", "benefits")
        ]
        description = "\n\n".join(
            section for section in sections if section
        )
        if not description:
            continue

        locations = []
        for value in detail.get("locations") or listed.get("locations") or []:
            location = _location(value)
            if location and location not in locations:
                locations.append(location)

        primary = _location(detail.get("location"))
        if not primary:
            primary = ", ".join(
                str(part).strip()
                for part in (
                    listed.get("city"),
                    listed.get("state"),
                    listed.get("country"),
                )
                if part
            ) or None
        if primary and primary not in locations:
            locations.insert(0, primary)

        department = listed.get("department")
        if not department:
            detail_departments = detail.get("department")
            if isinstance(detail_departments, list):
                department = " / ".join(
                    str(value).strip()
                    for value in detail_departments
                    if value
                ) or None
            elif detail_departments:
                department = str(detail_departments).strip() or None

        workplace = detail.get("workplace")
        if not workplace and (
            detail.get("remote") or listed.get("telecommuting")
        ):
            workplace = "remote"

        canonical_url = (
            listed.get("url")
            or listed.get("shortlink")
            or "https://apply.workable.com/j/" + str(shortcode)
        )
        apply_url = (
            listed.get("application_url")
            or "https://apply.workable.com/j/" + str(shortcode) + "/apply"
        )

        values = {
            "url": canonical_url,
            "title": detail.get("title") or listed.get("title"),
            "company": "Conduktor Inc",
            "location": primary,
            "locations": locations or None,
            "description": description,
            "source": SITE,
            "employment_type": (
                listed.get("employment_type") or detail.get("type")
            ),
            "workplace_type": workplace,
            "department": department,
            "posted_at": (
                detail.get("published")
                or listed.get("published_on")
                or listed.get("created_at")
            ),
            "external_id": str(shortcode),
            "apply_url": apply_url,
        }
        yield {
            key: value
            for key, value in values.items()
            if value is not None and value != ""
        }
