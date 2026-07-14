
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/filecoinfoundation/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    markup = html.unescape(value)
    markup = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        markup,
    )
    text = re.sub(r"<[^>]+>", "", markup)
    lines = []
    for line in text.splitlines():
        normalized = " ".join(html.unescape(line).split())
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for item in data.get("jobs", []):
        location_data = item.get("location") or {}
        offices = item.get("offices") or []
        departments = item.get("departments") or []

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "Filecoin Foundation",
            "location": location_data.get("name"),
            "description": _description(item.get("content") or ""),
            "source": SITE,
            "external_id": str(item["id"]),
        }

        locations = [
            office["name"]
            for office in offices
            if isinstance(office, dict) and office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        department_names = [
            department["name"]
            for department in departments
            if isinstance(department, dict) and department.get("name")
        ]
        if department_names:
            fields["department"] = ", ".join(department_names)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        yield fields
