import html
import json
import re

SITE = "https://semaphore.bamboohr.com/careers/list"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _location(record):
    primary = record.get("location") or {}
    parts = [
        primary.get("city"),
        primary.get("state"),
        primary.get("addressCountry"),
    ]
    parts = [part for part in parts if part]
    if not parts:
        ats = record.get("atsLocation") or {}
        parts = [
            ats.get("city"),
            ats.get("state") or ats.get("province"),
            ats.get("country"),
        ]
        parts = [part for part in parts if part]
    return ", ".join(dict.fromkeys(parts)) if parts else None


def crawl(ctx):
    board = json.loads(ctx.fetch(SITE))
    for summary in board.get("result", []):
        external_id = str(summary["id"])
        canonical_url = "https://semaphore.bamboohr.com/careers/" + external_id
        detail = json.loads(ctx.fetch(canonical_url + "/detail"))
        opening = detail["result"]["jobOpening"]

        if opening.get("jobOpeningStatus") != "Open":
            continue

        fields = {
            "url": opening.get("jobOpeningShareUrl") or canonical_url,
            "source": SITE,
            "apply_url": opening.get("jobOpeningShareUrl") or canonical_url,
            "title": opening.get("jobOpeningName")
                or summary.get("jobOpeningName"),
            "company": "Semaphore",
            "location": _location(opening) or _location(summary),
            "description": _plain_text(opening.get("description")),
            "external_id": external_id,
            "department": opening.get("departmentLabel")
                or summary.get("departmentLabel"),
            "employment_type": opening.get("employmentStatusLabel")
                or summary.get("employmentStatusLabel"),
            "posted_at": opening.get("datePosted"),
        }
        yield {
            key: value for key, value in fields.items()
            if value is not None
        }
