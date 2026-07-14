
import html
import json
import re
from datetime import datetime, timezone

SITE = 'https://api.lever.co/v0/postings/angellist?mode=json'
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = re.sub(r"<br\\s*/?>", "\\n", value, flags=re.I)
    value = re.sub(r"</(?:p|div|li|ul|ol|h[1-6])\\s*>", "\\n", value, flags=re.I)
    value = re.sub(r"<li(?:\\s[^>]*)?>", "- ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\\xa0", " ")
    value = re.sub(r"[ \\t]+\\n", "\\n", value)
    value = re.sub(r"\\n[ \\t]+", "\\n", value)
    value = re.sub(r"\\n{3,}", "\\n\\n", value)
    value = value.strip()
    return value or None


def _posted_at(value):
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = []

        introduction = posting.get("descriptionPlain") or _text(
            posting.get("description")
        )
        if introduction:
            sections.append(introduction.strip())

        for section in posting.get("lists") or []:
            heading = _text(section.get("text"))
            content = _text(section.get("content"))
            if heading and content:
                sections.append(heading + "\\n" + content)
            elif heading or content:
                sections.append(heading or content)

        additional = posting.get("additionalPlain") or _text(
            posting.get("additional")
        )
        if additional:
            sections.append(additional.strip())

        description = "\\n\\n".join(part for part in sections if part).strip()
        location = categories.get("location")
        all_locations = categories.get("allLocations")

        job = {
            "url": posting.get("hostedUrl"),
            "title": posting.get("text"),
            "company": "AngelList",
            "location": location,
            "description": description,
            "source": "Lever",
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "department": categories.get("team"),
            "posted_at": _posted_at(posting.get("createdAt")),
        }

        if isinstance(all_locations, list) and all_locations:
            job["locations"] = [
                item for item in all_locations
                if isinstance(item, str) and item
            ]

        yield {key: value for key, value in job.items() if value is not None}


