
import html
import json
import re


SITE = "https://gitkraken.bamboohr.com/careers/list"
EXTRA_HOSTS = []

def Job(**fields):
    return fields


_LOCATION_TYPES = {
    "0": "On-site",
    "1": "Remote",
    "2": "Hybrid",
}


def _plain_text(value):
    if not value:
        return None

    value = html.unescape(value)
    value = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        " ",
        value,
    )
    value = re.sub(
        r"(?i)<\s*(br|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|table|tr|section|article)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value or None


def _location(opening):
    primary = opening.get("location") or {}
    alternate = opening.get("atsLocation") or {}

    if any(primary.get(key) for key in ("city", "state", "addressCountry")):
        values = [
            primary.get("city"),
            primary.get("state"),
            primary.get("addressCountry"),
        ]
    else:
        values = [
            alternate.get("city"),
            alternate.get("state") or alternate.get("province"),
            alternate.get("country"),
        ]

    parts = []
    for value in values:
        if value and value not in parts:
            parts.append(value)

    if parts:
        return ", ".join(parts)

    if opening.get("locationType") == "1":
        return "Remote"

    return None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for listed in board.get("result", []):
        job_id = listed.get("id")
        if not job_id:
            continue

        detail_url = f"https://gitkraken.bamboohr.com/careers/{job_id}/detail"
        payload = json.loads(ctx.fetch(detail_url))
        opening = payload.get("result", {}).get("jobOpening", {})

        if opening.get("jobOpeningStatus") != "Open":
            continue

        title = opening.get("jobOpeningName")
        description = _plain_text(opening.get("description"))
        location = _location(opening)
        canonical_url = opening.get("jobOpeningShareUrl")

        if not title or not description or not location:
            continue

        yield Job(
            url=canonical_url or f"https://gitkraken.bamboohr.com/careers/{job_id}",
            source=SITE,
            title=title,
            company="GitKraken",
            location=location,
            description=description,
            employment_type=opening.get("employmentStatusLabel") or None,
            workplace_type=_LOCATION_TYPES.get(opening.get("locationType")),
            department=opening.get("departmentLabel") or None,
            posted_at=opening.get("datePosted") or None,
            external_id=str(job_id),
            apply_url=canonical_url or None,
        )
