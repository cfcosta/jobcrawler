
import html
import json
import re



SITE = "https://api.rippling.com/platform/api/ats/v1/board/celerdataopenroles/jobs"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    rows = json.loads(ctx.fetch(SITE))

    seen = set()
    for row in rows:
        external_id = row.get("uuid")
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)

        detail_url = SITE.rstrip("/") + "/" + external_id
        detail = json.loads(ctx.fetch(detail_url))

        description_data = detail.get("description") or {}
        description = "\n\n".join(
            part
            for part in (
                _plain_text(description_data.get("company")),
                _plain_text(description_data.get("role")),
            )
            if part
        )

        locations = [
            value.strip()
            for value in (detail.get("workLocations") or [])
            if isinstance(value, str) and value.strip()
        ]
        location = locations[0] if locations else None

        department_data = detail.get("department") or {}
        department = (
            department_data.get("name")
            or department_data.get("base_department")
        )

        employment_data = detail.get("employmentType") or {}
        employment_type = (
            employment_data.get("id")
            or employment_data.get("label")
        )

        canonical_url = detail.get("url") or row.get("url") or detail_url

        yield {
            "url": canonical_url,
            "apply_url": canonical_url,
            "title": detail.get("name") or row.get("name"),
            "company": detail.get("companyName") or "CelerData",
            "location": location,
            "locations": locations or None,
            "description": description,
            "department": department,
            "employment_type": employment_type,
            "posted_at": detail.get("createdOn"),
            "external_id": external_id,
            "source": SITE,
        }
