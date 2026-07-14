
import json
import re
from datetime import datetime, timezone
from html import unescape

SITE = "https://api.lever.co/v0/postings/whereby?mode=json"
EXTRA_HOSTS = []


def _html_text(value):
    if not value:
        return None
    value = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", value)
    value = re.sub(
        r"(?i)</?(?:div|p|li|ul|ol|h[1-6]|br)[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    lines = (" ".join(line.split()) for line in unescape(value).splitlines())
    text = "\n".join(line for line in lines if line).strip()
    return text or None


def _plain_or_html(posting, plain_key, html_key):
    value = posting.get(plain_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _html_text(posting.get(html_key))


def _posted_at(value):
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def crawl(ctx):
    body = ctx.fetch(SITE)
    postings = json.loads(body)

    for posting in postings:
        categories = posting.get("categories") or {}
        sections = []

        intro = _plain_or_html(posting, "descriptionPlain", "description")
        if intro:
            sections.append(intro)

        for section in posting.get("lists") or []:
            heading = (section.get("text") or "").strip()
            content = _html_text(section.get("content"))
            if heading and content:
                sections.append(heading + "\n" + content)
            elif heading:
                sections.append(heading)
            elif content:
                sections.append(content)

        additional = _plain_or_html(posting, "additionalPlain", "additional")
        if additional:
            sections.append(additional)

        description = "\n\n".join(sections).strip()
        all_locations = categories.get("allLocations")
        locations = None
        if isinstance(all_locations, list):
            locations = [
                item
                for item in all_locations
                if isinstance(item, str) and item.strip()
            ]

        workplace_type = posting.get("workplaceType")
        if workplace_type == "unspecified":
            workplace_type = None

        fields = {
            "url": posting.get("hostedUrl"),
            "source": SITE,
            "title": posting.get("text"),
            "company": "Whereby",
            "location": categories.get("location"),
            "description": description,
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "employment_type": categories.get("commitment"),
            "department": categories.get("team"),
            "workplace_type": workplace_type,
        }
        if locations:
            fields["locations"] = locations

        yield {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
