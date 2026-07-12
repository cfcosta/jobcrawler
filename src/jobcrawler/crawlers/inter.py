
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/inter/jobs?content=true"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    # Greenhouse content is HTML-entity-escaped; decode it before removing tags.
    value = html.unescape(value)
    value = re.sub(
        r"(?is)<(?:br|hr|/p|/div|/li|/tr|/h[1-6]|/section|/article|/ul|/ol)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        fields = {
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Inter Carreiras",
            "location": (item.get("location") or {}).get("name"),
            "description": _html_to_text(item.get("content")),
            "source": SITE,
        }

        if item.get("id") is not None:
            fields["external_id"] = str(item["id"])
        if item.get("absolute_url"):
            fields["apply_url"] = item["absolute_url"]
        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]

        offices = [
            office.get("name")
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)
        else:
            for metadata in item.get("metadata") or []:
                if metadata.get("name") in ("Área", "Area"):
                    values = metadata.get("value")
                    if isinstance(values, list):
                        values = ", ".join(str(value) for value in values if value)
                    if values:
                        fields["department"] = str(values)
                    break

        yield fields
