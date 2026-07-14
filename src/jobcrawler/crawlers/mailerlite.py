import html
import json
import re

SITE = "https://mailerlite.recruitee.com/api/offers/"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)\s*>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _timestamp(value):
    if not value:
        return None
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC",
        value,
    )
    if match:
        return match.group(1) + "T" + match.group(2) + "Z"
    return value


def _workplace_type(offer):
    if offer.get("remote"):
        return "remote"
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("on_site"):
        return "on-site"
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for offer in payload.get("offers", []):
        if offer.get("status") != "published":
            continue

        body_parts = [
            part
            for part in (offer.get("description"), offer.get("requirements"))
            if part
        ]
        description = _html_to_text("\n".join(body_parts))
        url = offer.get("careers_url")
        title = offer.get("title")
        location = (
            offer.get("location")
            or offer.get("city")
            or offer.get("country")
        )

        if not title or not description or not url or not location:
            continue

        data = {
            "url": url,
            "title": title,
            "company": "MailerLite",
            "location": location,
            "description": description,
            "external_id": (
                str(offer["id"]) if offer.get("id") is not None else None
            ),
            "apply_url": offer.get("careers_apply_url"),
            "department": offer.get("department"),
            "employment_type": offer.get("employment_type_code"),
            "workplace_type": _workplace_type(offer),
            "posted_at": _timestamp(offer.get("published_at")),
            "expires_at": _timestamp(offer.get("close_at")),
        }

        locations = [
            item["name"]
            for item in (offer.get("locations") or [])
            if isinstance(item, dict) and item.get("name")
        ]
        if locations:
            data["locations"] = locations

        salary = offer.get("salary") or {}
        if salary.get("min") is not None:
            data["salary_min"] = salary["min"]
        if salary.get("max") is not None:
            data["salary_max"] = salary["max"]
        if salary.get("currency"):
            data["salary_currency"] = salary["currency"]
        if salary.get("period"):
            data["salary_period"] = salary["period"]

        data["source"] = SITE
        yield {
            key: value
            for key, value in data.items()
            if value is not None
        }
