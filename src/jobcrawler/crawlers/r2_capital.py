
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://r2.bamboohr.com/careers/list"
EXTRA_HOSTS = []

COMPANY = "R2"
SOURCE = "bamboohr"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = _WS_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _title_location(title):
    if not title:
        return None
    match = re.search(r"\(([^()]*)\)\s*$", title.strip())
    if match:
        loc = match.group(1).strip()
        return loc or None
    return None


def _structured_location(opening):
    parts = []
    for key in ("location", "atsLocation"):
        loc = opening.get(key) or {}
        for field in ("city", "state", "province", "country", "addressCountry"):
            val = loc.get(field)
            if val and val not in parts:
                parts.append(str(val))
    return ", ".join(parts) if parts else None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    for item in board.get("result") or []:
        job_id = str(item.get("id") or "").strip()
        if not job_id:
            continue

        detail_url = urljoin(SITE, "/careers/%s/detail" % job_id)
        detail = json.loads(ctx.fetch(detail_url))
        opening = (detail.get("result") or {}).get("jobOpening") or item

        if opening.get("jobOpeningStatus") and opening.get("jobOpeningStatus") != "Open":
            continue

        title = (opening.get("jobOpeningName") or item.get("jobOpeningName") or "").strip()
        description = _clean_html(opening.get("description"))
        canonical = opening.get("jobOpeningShareUrl") or urljoin(SITE, "/careers/%s" % job_id)

        location = _structured_location(opening) or _title_location(title) or "Not specified"

        job = {
            "url": canonical,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "external_id": job_id,
            "source": SOURCE,
        }

        department = opening.get("departmentLabel") or item.get("departmentLabel")
        if department:
            job["department"] = department

        employment_type = opening.get("employmentStatusLabel") or item.get("employmentStatusLabel")
        if employment_type:
            job["employment_type"] = employment_type

        yield job
