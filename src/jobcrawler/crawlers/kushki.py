
import html
import json
import re


SITE = "https://kushkipagos.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.replace("\xa0", " ").split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _location(job, row):
    location = job.get("location") or row.get("location") or {}
    ats_location = job.get("atsLocation") or row.get("atsLocation") or {}

    city = location.get("city") or ats_location.get("city")
    state = location.get("state") or ats_location.get("state")
    country = location.get("addressCountry") or ats_location.get("country")

    parts = []
    for value in (city, state, country):
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts) if parts else None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))

    for row in board.get("result", []):
        job_id = row.get("id")
        if not job_id:
            continue

        detail_url = f"https://kushkipagos.bamboohr.com/careers/{job_id}/detail"
        payload = json.loads(ctx.fetch(detail_url))
        job = (payload.get("result") or {}).get("jobOpening") or {}

        if job.get("jobOpeningStatus") != "Open":
            continue

        title = (
            job.get("jobOpeningName")
            or row.get("jobOpeningName")
            or ""
        ).strip()
        description = _plain_text(job.get("description"))
        location = _location(job, row)
        if not title or not description or not location:
            continue

        canonical_url = (
            job.get("jobOpeningShareUrl")
            or f"https://kushkipagos.bamboohr.com/careers/{job_id}"
        )

        fields = {
            "site": SITE,
            "source": SITE,
            "url": canonical_url,
            "apply_url": canonical_url,
            "title": title,
            "company": "Kushki",
            "location": location,
            "description": description,
            "external_id": str(job_id),
        }

        optional = {
            "department": (
                job.get("departmentLabel")
                or row.get("departmentLabel")
            ),
            "employment_type": (
                job.get("employmentStatusLabel")
                or row.get("employmentStatusLabel")
            ),
            "posted_at": job.get("datePosted"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        yield fields
