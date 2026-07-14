
import html
import json
import re

SITE = "https://hygraph.recruitee.com/api/offers/"
EXTRA_HOSTS = []


def _plain_text(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(
        r"(?i)<\s*(br|p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|blockquote|tr)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    lines = []
    for line in value.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines) or None


def _location_names(offer):
    names = []
    for location in offer.get("locations") or []:
        name = location.get("name")
        if name:
            names.append(name)
    return names or None


def _timestamp(value):
    if not value:
        return None
    if value.endswith(" UTC"):
        return value[:-4].replace(" ", "T") + "Z"
    return value


def _workplace_type(offer):
    if offer.get("hybrid"):
        return "hybrid"
    if offer.get("remote"):
        return "remote"
    if offer.get("on_site"):
        return "on_site"
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))

    for offer in payload.get("offers") or []:
        description_parts = [
            text
            for text in (
                _plain_text(offer.get("description")),
                _plain_text(offer.get("requirements")),
            )
            if text
        ]
        description = "\n\n".join(description_parts)
        if not offer.get("title") or not description:
            continue

        fields = {
            "url": offer.get("careers_url") or SITE,
            "title": offer["title"],
            "company": "Hygraph",
            "location": offer.get("location"),
            "description": description,
            "source": SITE,
        }

        optional = {
            "locations": _location_names(offer),
            "department": offer.get("department"),
            "employment_type": offer.get("employment_type_code"),
            "workplace_type": _workplace_type(offer),
            "posted_at": _timestamp(offer.get("published_at")),
            "expires_at": _timestamp(offer.get("close_at")),
            "external_id": str(offer["id"]) if offer.get("id") is not None else None,
            "apply_url": offer.get("careers_apply_url"),
        }
        for key, value in optional.items():
            if value is not None:
                fields[key] = value

        salary = offer.get("salary") or {}
        if salary.get("min") is not None:
            fields["salary_min"] = float(salary["min"])
        if salary.get("max") is not None:
            fields["salary_max"] = float(salary["max"])
        if salary.get("currency"):
            fields["salary_currency"] = salary["currency"]
        if salary.get("period"):
            fields["salary_period"] = salary["period"]

        yield fields
