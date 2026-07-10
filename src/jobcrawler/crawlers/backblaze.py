
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/backblaze/jobs?content=true"
EXTRA_HOSTS = []


def clean_description(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>|</div\s*>|"
        r"</section\s*>|</tr\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        normalized = " ".join(line.split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        url = item.get("absolute_url")
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = clean_description(item.get("content"))

        if not url or not title or not location or not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Backblaze",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if item.get("id") is not None:
            job["external_id"] = str(item["id"])

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]

        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            job["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            job["department"] = "; ".join(departments)

        yield job
