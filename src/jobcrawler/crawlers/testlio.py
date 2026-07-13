import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/testlio/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>",
        "",
        value,
    )
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", value)
    value = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|figcaption|footer|"
        r"h[1-6]|header|hr|main|ol|p|pre|section|table|tbody|td|th|thead|tr|ul)"
        r"\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        description = _plain_text(item.get("content"))
        url = item.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Testlio",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            fields["expires_at"] = item["application_deadline"]

        yield fields
