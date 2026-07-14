
import html
import json
import re
from urllib.parse import urljoin, urlparse


SITE = "https://leastauthority.softgarden.io/en/vacancies"
EXTRA_HOSTS = []


def _attribute(tag, name):
    match = re.search(
        r"\b" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1",
        tag,
        flags=re.I | re.S,
    )
    return html.unescape(match.group(2)) if match else None


def _board_links(page):
    links = []
    for match in re.finditer(r"(<a\b[^>]*>)(.*?)</a\s*>", page, flags=re.I | re.S):
        href = _attribute(match.group(1), "href")
        if not href or not re.search(r"/job/\d+/", href):
            continue
        title = re.sub(r"<[^>]+>", " ", match.group(2))
        title = " ".join(html.unescape(title).split())
        links.append((href, title))
    return links


def _json_ld(page):
    pattern = (
        r"<script\b[^>]*\btype\s*=\s*([\"'])"
        r"application/ld\+json\1[^>]*>(.*?)</script\s*>"
    )
    for match in re.finditer(pattern, page, flags=re.I | re.S):
        raw = match.group(2).strip()
        try:
            yield json.loads(raw)
        except (TypeError, ValueError):
            continue


def _job_posting(value):
    pending = value if isinstance(value, list) else [value]
    pending = list(pending)

    while pending:
        item = pending.pop(0)
        if isinstance(item, list):
            pending.extend(item)
            continue
        if not isinstance(item, dict):
            continue

        item_type = item.get("@type")
        if item_type == "JobPosting":
            return item
        if isinstance(item_type, list) and "JobPosting" in item_type:
            return item

        graph = item.get("@graph")
        if isinstance(graph, list):
            pending.extend(graph)

    return None


def _description_text(value):
    if not value:
        return None

    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6])\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", " ", text,
                  flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _location(posting):
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return None

    address = location.get("address")
    if isinstance(address, str):
        return address.strip() or None
    if not isinstance(address, dict):
        return None

    parts = []
    for key in ("addressLocality", "addressRegion", "addressCountry"):
        value = address.get(key)
        if isinstance(value, dict):
            value = value.get("name")
        if not value:
            continue
        value = str(value).strip()
        if value != "-" and value not in parts:
            parts.append(value)

    return ", ".join(parts) or None


def _canonical(page, fallback):
    for tag in re.findall(r"<link\b[^>]*>", page, flags=re.I | re.S):
        rel = _attribute(tag, "rel")
        href = _attribute(tag, "href")
        if rel and href and "canonical" in rel.lower().split():
            return urljoin(fallback, href)
    return fallback


def _apply_url(page, base):
    for tag in re.findall(r"<a\b[^>]*>", page, flags=re.I | re.S):
        href = _attribute(tag, "href")
        if href and "/jobposting/applyonline/click" in href:
            return urljoin(base, href)
    return None


def crawl(ctx):
    board_page = ctx.fetch(SITE)
    seen_ids = set()

    for href, listing_title in _board_links(board_page):
        detail_url = urljoin(SITE, href)
        match = re.search(r"/job/(\d+)/", urlparse(detail_url).path)
        if not match:
            continue

        external_id = match.group(1)
        if external_id in seen_ids:
            continue
        seen_ids.add(external_id)

        page = ctx.fetch(detail_url)
        posting = None
        for structured_data in _json_ld(page):
            posting = _job_posting(structured_data)
            if posting:
                break
        if not posting:
            continue

        title = posting.get("title") or listing_title
        description = _description_text(posting.get("description"))
        location = _location(posting)
        if not title or not description or not location:
            continue

        employment_type = posting.get("employmentType")
        if isinstance(employment_type, list):
            if len(employment_type) == 1:
                employment_type = employment_type[0]
            elif employment_type:
                employment_type = ", ".join(employment_type)
            else:
                employment_type = None

        location_type = posting.get("jobLocationType")
        workplace_type = None
        if location_type == "TELECOMMUTE":
            workplace_type = "remote"
        elif location_type:
            workplace_type = location_type

        fields = {
            "url": _canonical(page, detail_url),
            "title": title,
            "company": "Least Authority",
            "location": location,
            "description": description,
            "external_id": external_id,
            "posted_at": posting.get("datePosted"),
            "expires_at": posting.get("validThrough"),
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "apply_url": _apply_url(page, detail_url),
            "source": SITE,
        }
        yield {key: value for key, value in fields.items() if value is not None}
