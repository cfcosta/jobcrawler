
import html
import json
import re

SITE = "https://apply.workable.com/api/v1/widget/accounts/tatum"
EXTRA_HOSTS = []


def _plain_text(value):
    text = value or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(
        r"(?i)</?(?:p|div|ul|ol|li|h[1-6]|section|article|blockquote|tr)(?:\s[^>]*)?>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _location(value):
    parts = [
        value.get("city"),
        value.get("region"),
        value.get("country"),
    ]
    return ", ".join(part for part in parts if part)


def crawl(ctx):
    response = ctx.fetch(SITE + "?details=true")
    data = json.loads(response)
    company = data.get("name") or "Tatum"
    seen = set()

    for item in data.get("jobs") or []:
        shortcode = item.get("shortcode")
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)

        description = _plain_text(item.get("description"))
        if not description:
            continue

        structured_locations = []
        for place in item.get("locations") or []:
            text = _location(place)
            if text and text not in structured_locations:
                structured_locations.append(text)

        primary = _location({
            "city": item.get("city"),
            "region": item.get("state"),
            "country": item.get("country"),
        })
        if not primary and structured_locations:
            primary = structured_locations[0]

        job = {
            "source": SITE,
            "url": item.get("url"),
            "title": item.get("title"),
            "company": company,
            "location": primary,
            "description": description,
            "external_id": shortcode,
        }
        if structured_locations:
            job["locations"] = structured_locations
        if item.get("application_url"):
            job["apply_url"] = item["application_url"]
        if item.get("department"):
            job["department"] = item["department"]
        if item.get("employment_type"):
            job["employment_type"] = item["employment_type"]
        if item.get("published_on"):
            job["posted_at"] = item["published_on"]
        if item.get("telecommuting") is True:
            job["workplace_type"] = "remote"

        yield job
