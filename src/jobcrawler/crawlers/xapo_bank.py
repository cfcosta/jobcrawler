
import json
import re
from html import unescape


SITE = "https://boards-api.greenhouse.io/v1/boards/xapo61/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    seen = set()

    for posting in payload.get("jobs", []):
        job_id = posting.get("id")
        if job_id is None or job_id in seen:
            continue
        seen.add(job_id)

        title = posting.get("title")
        url = posting.get("absolute_url")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content") or "")
        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Xapo Bank",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job_id),
        }

        locations = [
            office.get("name")
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        posted_at = posting.get("first_published")
        if posted_at:
            fields["posted_at"] = posted_at

        expires_at = posting.get("application_deadline")
        if expires_at:
            fields["expires_at"] = expires_at

        yield fields
