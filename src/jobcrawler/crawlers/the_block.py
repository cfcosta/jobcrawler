
import json
import re
from datetime import datetime, timezone
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = 'https://api.lever.co/v0/postings/theblockcrypto?mode=json'
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    text = str(value)
    text = re.sub(
        r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<(?:br|hr)\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|dd|div|dl|dt|figcaption|figure|"
        r"footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tbody|td|"
        r"tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")

    lines = [
        re.sub(" +", " ", line.replace(chr(9), " ")).strip()
        for line in text.split("\n")
    ]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _posted_at(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(
            float(value) / 1000.0, timezone.utc
        ).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _full_description(posting):
    sections = []

    intro = _html_to_text(posting.get("description"))
    if intro:
        sections.append(intro)

    lists = posting.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if not isinstance(section, dict):
                continue

            pieces = []
            heading = section.get("text")
            if isinstance(heading, str) and heading.strip():
                pieces.append(heading.strip())

            content = _html_to_text(section.get("content"))
            if content:
                pieces.append(content)

            if pieces:
                sections.append("\n".join(pieces))

    additional = _html_to_text(posting.get("additional"))
    if additional:
        sections.append(additional)

    return "\n\n".join(sections).strip() or None


def crawl(ctx):
    postings = json.loads(ctx.fetch(SITE))
    if not isinstance(postings, list):
        return

    for posting in postings:
        if not isinstance(posting, dict):
            continue

        categories = posting.get("categories")
        if not isinstance(categories, dict):
            categories = {}

        title = posting.get("text")
        description = _full_description(posting)
        url = posting.get("hostedUrl")
        location = categories.get("location")

        locations = None
        all_locations = categories.get("allLocations")
        if isinstance(all_locations, list):
            locations = [
                item
                for item in all_locations
                if isinstance(item, str) and item.strip()
            ] or None
            if not location and locations:
                location = locations[0]

        if not (
            isinstance(title, str)
            and title.strip()
            and isinstance(description, str)
            and description.strip()
            and isinstance(url, str)
            and url.strip()
            and isinstance(location, str)
            and location.strip()
        ):
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "The Block",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": locations,
            "department": categories.get("team"),
            "employment_type": categories.get("commitment"),
            "workplace_type": posting.get("workplaceType"),
            "posted_at": _posted_at(posting.get("createdAt")),
            "external_id": posting.get("id"),
            "apply_url": posting.get("applyUrl"),
        }
        for key, value in optional.items():
            if value is not None and value != "":
                fields[key] = value

        yield Job(**fields)
