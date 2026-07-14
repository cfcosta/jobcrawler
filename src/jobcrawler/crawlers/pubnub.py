import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/pubnub/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?is)<(?:br\s*/?|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        "",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for item in payload.get("jobs", []):
        title = item.get("title")
        location = (item.get("location") or {}).get("name")
        url = item.get("absolute_url")
        description = _plain_text(item.get("content"))
        if not title or not location or not url or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "PubNub",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]),
        }

        locations = [
            office["name"]
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

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
