
import html
import json
import re



SITE = "https://boards-api.greenhouse.io/v1/boards/pinwheelapi/jobs?content=true"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    payload = ctx.fetch(SITE)
    data = json.loads(payload)

    for item in data.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _text(item.get("content"))
        if not item.get("title") or not location or not description:
            continue

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": item.get("company_name") or "Pinwheel",
            "location": location,
            "description": description,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
        }

        offices = [
            office["name"]
            for office in item.get("offices", [])
            if isinstance(office, dict) and office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments", [])
            if isinstance(department, dict) and department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        yield {key: value for key, value in fields.items() if value is not None}
