
import html
import json
import re

SITE = "https://www.mullvad.careers/jobs.json"
EXTRA_HOSTS = []


def _html_to_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<\s*(?:br|hr)\b[^>]*>|</\s*(?:address|article|aside|blockquote|dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", "", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        clean = " ".join(line.split())
        if clean:
            lines.append(clean)
    return "\n".join(lines)


def _location_name(place):
    address = (place or {}).get("address") or {}
    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(key)
        if value and value not in parts:
            parts.append(str(value))
    return ", ".join(parts)


def crawl(ctx):
    url = SITE
    seen_urls = set()

    while url:
        feed = json.loads(ctx.fetch(url))
        for item in feed.get("items") or []:
            job_url = item.get("url")
            if not job_url or job_url in seen_urls:
                continue
            seen_urls.add(job_url)

            posting = item.get("_jobposting") or {}
            places = posting.get("jobLocation") or []
            if isinstance(places, dict):
                places = [places]

            locations = [name for name in map(_location_name, places) if name]
            if not locations:
                continue

            identifier = posting.get("identifier") or {}
            if isinstance(identifier, dict):
                external_id = identifier.get("value")
            else:
                external_id = identifier

            description_html = item.get("content_html") or posting.get("description")
            description = _html_to_text(description_html)
            title = item.get("title") or posting.get("title")
            if not title or not description:
                continue

            yield {
                "url": job_url,
                "title": title,
                "company": "Mullvad VPN",
                "location": locations[0],
                "locations": locations,
                "description": description,
                "posted_at": item.get("date_published") or posting.get("datePosted"),
                "external_id": str(external_id) if external_id is not None else None,
                "apply_url": job_url,
                "source": SITE,
            }

        url = feed.get("next_url")
