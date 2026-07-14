
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/corelight/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table|/section|/article)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]*>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        departments = [
            entry["name"]
            for entry in item.get("departments", [])
            if entry.get("name")
        ]
        locations = [
            entry["name"]
            for entry in item.get("offices", [])
            if entry.get("name")
        ]

        employment_type = None
        for field in item.get("metadata", []):
            if (
                field.get("name") == "Workforce Classification"
                and field.get("value")
            ):
                employment_type = field["value"]
                break

        job = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": "Corelight",
            "location": location,
            "description": _plain_text(item.get("content")),
            "source": "greenhouse",
            "external_id": str(item["id"]),
            "apply_url": item["absolute_url"],
        }

        if locations:
            job["locations"] = locations
        if departments:
            job["department"] = ", ".join(departments)
        if employment_type:
            job["employment_type"] = employment_type
        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        yield job
