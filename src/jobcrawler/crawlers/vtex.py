
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/vtex/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)</?(?:br|p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        title = item.get("title")
        description = _description(item.get("content"))
        url = item.get("absolute_url")
        if not all((url, title, location, description)):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "VTEX",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
            "apply_url": url,
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        yield fields
