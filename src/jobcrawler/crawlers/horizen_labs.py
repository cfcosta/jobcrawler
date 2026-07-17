
import html
import json
import re
from jobcrawler.runtime.contract import Job


SITE = "https://boards-api.greenhouse.io/v1/boards/horizenlabs/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    if not value:
        return None

    text = html.unescape(value)
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:p|div|li|ul|ol|h[1-6]|section|article|table|tr)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [
        re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        for line in text.split("\n")
    ]
    return "\n".join(line for line in lines if line) or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for posting in payload.get("jobs", []):
        location_data = posting.get("location") or {}
        offices = posting.get("offices") or []
        departments = posting.get("departments") or []

        kwargs = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Horizen Labs",
            "location": location_data.get("name"),
            "description": _description(posting.get("content")),
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
        }

        locations = [
            office["name"]
            for office in offices
            if isinstance(office, dict) and office.get("name")
        ]
        if locations:
            kwargs["locations"] = locations

        department_names = [
            department["name"]
            for department in departments
            if isinstance(department, dict) and department.get("name")
        ]
        if department_names:
            kwargs["department"] = ", ".join(department_names)

        yield Job(
            **{
                key: value
                for key, value in kwargs.items()
                if value is not None and value != ""
            }
        )
