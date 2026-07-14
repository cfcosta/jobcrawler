
import json
import re
from html import unescape

SITE = "https://www.deltastream.io/wp-json/wp/v2/career?per_page=100"
EXTRA_HOSTS = []


def clean_html(value):
    value = value or ""
    value = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/h[1-6])\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    return "\n".join(line.strip() for line in value.splitlines() if line.strip())


def crawl(ctx):
    page = 1

    while True:
        url = SITE if page == 1 else SITE + "&page=" + str(page)
        raw = ctx.fetch(url)
        roles = json.loads(raw)

        for role in roles:
            if role.get("status") != "publish":
                continue

            title = clean_html(role.get("title", {}).get("rendered"))
            description = clean_html(role.get("content", {}).get("rendered"))
            lines = description.splitlines()

            location = None
            employment_type = None
            if len(lines) > 1 and "|" in lines[1]:
                location_text, employment_text = lines[1].split("|", 1)
                location = location_text.strip() or None
                employment_type = employment_text.strip() or None

            canonical_url = role.get("link")
            yield dict(
                url=canonical_url,
                title=title,
                company="DeltaStream",
                location=location,
                description=description,
                source=SITE,
                employment_type=employment_type,
                posted_at=role.get("date_gmt") or role.get("date"),
                external_id=str(role["id"]) if role.get("id") is not None else None,
                apply_url=canonical_url,
            )

        if len(roles) < 100:
            break
        page += 1
