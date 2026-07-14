Job = dict

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/discord/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<\s*(?:br|hr|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script\s*>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style\s*>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    if hasattr(response, "json"):
        payload = response.json()
    else:
        payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        locations = [
            office["name"]
            for office in (item.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in (item.get("departments") or [])
            if department.get("name")
        ]

        fields = {
            "source": SITE,
            "url": item.get("absolute_url"),
            "title": item.get("title"),
            "company": item.get("company_name") or "Discord",
            "location": location,
            "locations": locations or None,
            "description": _description(item.get("content")),
            "department": ", ".join(departments) if departments else None,
            "posted_at": item.get("first_published"),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "apply_url": item.get("absolute_url"),
        }
        yield Job(**{
            key: value
            for key, value in fields.items()
            if value is not None
        })
