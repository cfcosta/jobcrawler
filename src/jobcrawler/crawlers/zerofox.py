import json
import re


SITE = "https://apply.workable.com/api/v1/widget/accounts/zerofox"
EXTRA_HOSTS = []
DISCOVERY_URL = SITE + "?details=true"


def _text(value):
    text = value or ""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*li(?:\s[^>]*)?>", "\n- ", text, flags=re.I)
    text = re.sub(
        r"</?\s*(?:p|div|section|article|h[1-6]|ul|ol|table|tr|td|th|blockquote|pre)(?:\s[^>]*)?>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    entities = {
        "&nbsp;": " ",
        "&#160;": " ",
        "&#xA0;": " ",
        "&amp;": "&",
        "&#38;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#34;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "&ndash;": "-",
        "&mdash;": "-",
        "&rsquo;": "'",
        "&lsquo;": "'",
        "&rdquo;": '"',
        "&ldquo;": '"',
        "&bull;": "-",
    }
    for entity, replacement in entities.items():
        text = text.replace(entity, replacement)
    text = re.sub(
        r"&#(\d+);",
        lambda match: chr(int(match.group(1))),
        text,
    )
    text = re.sub(
        r"&#x([0-9a-fA-F]+);",
        lambda match: chr(int(match.group(1), 16)),
        text,
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _location(parts):
    return ", ".join(
        str(part).strip()
        for part in parts
        if part is not None and str(part).strip()
    )


def _locations(item):
    values = []
    for place in item.get("locations") or []:
        value = _location(
            (place.get("city"), place.get("region"), place.get("country"))
        )
        if value and value not in values:
            values.append(value)
    return values


def crawl(ctx):
    board = json.loads(ctx.fetch(DISCOVERY_URL))
    for item in board.get("jobs") or []:
        title = (item.get("title") or "").strip()
        description = _text(item.get("description"))
        url = item.get("url") or item.get("shortlink")
        location = _location(
            (item.get("city"), item.get("state"), item.get("country"))
        )
        locations = _locations(item)

        if not title or not description or not url or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "ZeroFox",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": item.get("shortcode"),
            "apply_url": item.get("application_url"),
            "department": item.get("department"),
            "employment_type": item.get("employment_type"),
            "posted_at": item.get("published_on"),
            "locations": locations or None,
            "workplace_type": "Remote"
            if item.get("telecommuting") is True
            else None,
        }
        yield {
            key: value
            for key, value in fields.items()
            if value not in (None, "", [])
        }
