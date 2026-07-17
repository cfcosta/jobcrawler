
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/okx/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", text)
    text = re.sub(
        r"(?i)<(?:br|p|div|li|h[1-6]|tr|section|article|blockquote|pre|ul|ol)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(
        r"(?i)</(?:p|div|li|h[1-6]|tr|section|article|blockquote|pre|ul|ol)>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for item in payload.get("jobs", []):
        departments = [
            department["name"]
            for department in item.get("departments") or []
            if department.get("name")
        ]
        offices = [
            office["name"]
            for office in item.get("offices") or []
            if office.get("name")
        ]

        fields = {
            "url": item["absolute_url"],
            "title": item["title"],
            "company": item.get("company_name") or "OKX",
            "location": (item.get("location") or {}).get("name"),
            "description": _plain_text(item.get("content")),
            "source": "Greenhouse",
            "external_id": str(item["id"]),
        }

        if item.get("first_published"):
            fields["posted_at"] = item["first_published"]
        if offices:
            fields["locations"] = offices
        if departments:
            fields["department"] = departments[0]

        yield Job(**fields)
