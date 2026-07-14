
import json
import re
from html import unescape


SITE = "https://apply.workable.com/api/v1/widget/accounts/zodia-custody"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:div|h[1-6]|ol|p|pre|section|table|td|th|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _location_name(location):
    if not isinstance(location, dict):
        if isinstance(location, str) and location.strip():
            return location.strip()
        return None

    parts = [
        location.get("city"),
        location.get("region") or location.get("state"),
        location.get("country"),
    ]
    parts = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return ", ".join(parts) or None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    company = board.get("name") or "Zodia Custody"

    for summary in board.get("jobs", []):
        shortcode = summary.get("shortcode")
        if not shortcode:
            continue

        detail_url = (
            "https://apply.workable.com/api/v1/accounts/"
            "zodia-custody/jobs/" + shortcode
        )
        detail = json.loads(ctx.fetch(detail_url))

        if detail.get("state") != "published":
            continue

        sections = []
        for field in ("description", "requirements", "benefits"):
            text = _html_to_text(detail.get(field))
            if text:
                sections.append(text)
        description = "\n\n".join(sections)

        source_locations = detail.get("locations") or summary.get("locations") or []
        locations = [
            location
            for location in (_location_name(item) for item in source_locations)
            if location
        ]
        primary_location = _location_name(detail.get("location"))
        if not primary_location:
            primary_location = locations[0] if locations else None

        department = detail.get("department") or summary.get("department")
        if isinstance(department, list):
            department = ", ".join(
                value.strip()
                for value in department
                if isinstance(value, str) and value.strip()
            ) or None

        salary = None
        benefits_text = _html_to_text(detail.get("benefits"))
        if benefits_text:
            for line in benefits_text.splitlines():
                printed = line.lstrip("- ").strip()
                if "salary" in printed.lower():
                    salary = printed
                    break

        workplace = detail.get("workplace")
        if not workplace and detail.get("remote") is True:
            workplace = "remote"

        yield dict(
            source=SITE,
            url=summary.get("url") or summary.get("shortlink"),
            title=detail.get("title") or summary.get("title"),
            company=company,
            location=primary_location,
            locations=locations or None,
            description=description,
            employment_type=summary.get("employment_type"),
            salary=salary,
            workplace_type=workplace,
            department=department,
            posted_at=detail.get("published") or summary.get("published_on"),
            external_id=shortcode,
            apply_url=summary.get("application_url"),
        )
