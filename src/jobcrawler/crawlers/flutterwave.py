import json
import re
from html import unescape

SITE = "https://flutterwavego.bamboohr.com/careers/list"
EXTRA_HOSTS = []

_BASE = "https://flutterwavego.bamboohr.com/careers"
_BLOCK_TAGS = re.compile(
    r"</?(?:br|p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_HORIZONTAL_SPACE = re.compile(r"[ \t\f\v]+")
_EXTRA_NEWLINES = re.compile(r"\n{3,}")


def _html_to_text(value):
    text = value or ""
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAGS.sub(" ", text)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _EXTRA_NEWLINES.sub("\n\n", text).strip()


def _location(opening, listed):
    ats = opening.get("atsLocation") or listed.get("atsLocation") or {}
    parts = []
    for key in ("country", "state", "province", "city"):
        value = ats.get(key)
        if value and value not in parts:
            parts.append(value)
    if parts:
        return ", ".join(parts)

    place = opening.get("location") or listed.get("location") or {}
    parts = []
    for key in ("city", "state", "addressCountry"):
        value = place.get(key)
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for listed in board.get("result") or []:
        job_id = str(listed.get("id") or "").strip()
        if not job_id:
            continue

        detail = json.loads(ctx.fetch(f"{_BASE}/{job_id}/detail"))
        opening = ((detail.get("result") or {}).get("jobOpening") or {})
        if opening.get("jobOpeningStatus") not in (None, "Open"):
            continue

        title = opening.get("jobOpeningName") or listed.get("jobOpeningName")
        description = _html_to_text(opening.get("description"))
        location = _location(opening, listed)
        if not title or not description or not location:
            continue

        url = opening.get("jobOpeningShareUrl") or f"{_BASE}/{job_id}"
        fields = {
            "url": url,
            "title": title,
            "company": "Flutterwave",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": job_id,
            "apply_url": f"{_BASE}/{job_id}",
        }

        department = opening.get("departmentLabel") or listed.get("departmentLabel")
        if department:
            fields["department"] = department

        employment_type = (
            opening.get("employmentStatusLabel")
            or listed.get("employmentStatusLabel")
        )
        if employment_type:
            fields["employment_type"] = employment_type

        posted_at = opening.get("datePosted")
        if posted_at:
            fields["posted_at"] = posted_at

        location_type = opening.get("locationType")
        if location_type is None:
            location_type = listed.get("locationType")
        if str(location_type) == "1" or listed.get("isRemote") is True:
            fields["workplace_type"] = "remote"

        compensation = opening.get("compensation")
        if isinstance(compensation, str) and compensation.strip():
            fields["salary"] = compensation.strip()

        yield fields
