import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SITE = "https://ats.rippling.com/api/v2/board/argyle/jobs?page=0&pageSize=1000"
EXTRA_HOSTS = []


def _to_text(markup):
    text = markup or ""
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?\s*(?:article|blockquote|div|h[1-6]|ol|p|section|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<\s*li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</\s*li\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    )


def _page_url(page):
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _next_data(page_html):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("Rippling posting page has no Next.js payload")
    return json.loads(match.group(1))


def _salary_text(description):
    for line in description.splitlines():
        match = re.match(r"Compensation\s*:\s*(.+)", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def crawl(ctx):
    page_number = 0

    while True:
        response = ctx.fetch(_page_url(page_number))
        listing = json.loads(response)
        items = listing.get("items") or []

        for item in items:
            detail_response = ctx.fetch(item["url"])
            data = _next_data(detail_response)
            api_data = data["props"]["pageProps"]["apiData"]
            post = api_data["jobPost"]

            sections = post.get("description") or {}
            description_parts = [
                _to_text(sections.get(name))
                for name in ("company", "role")
                if sections.get(name)
            ]
            description = "\n\n".join(
                part for part in description_parts if part
            )

            listing_locations = item.get("locations") or []
            locations = [
                location["name"]
                for location in listing_locations
                if location.get("name")
            ]
            if not locations:
                locations = [
                    location for location in (post.get("workLocations") or [])
                    if location
                ]

            workplace_types = {
                location.get("workplaceType")
                for location in listing_locations
                if location.get("workplaceType")
            }
            workplace_type = (
                next(iter(workplace_types)) if len(workplace_types) == 1 else None
            )

            department_data = (
                post.get("department") or api_data.get("department") or {}
            )
            employment_data = post.get("employmentType") or {}
            canonical_url = post.get("url") or item["url"]

            fields = {
                "url": canonical_url,
                "title": post.get("name") or item["name"],
                "company": "Argyle",
                "location": locations[0] if locations else "Remote",
                "locations": locations or None,
                "description": description,
                "external_id": post.get("uuid") or item.get("id"),
                "apply_url": canonical_url.rstrip("/") + "/apply",
                "department": department_data.get("name"),
                "employment_type": (
                    employment_data.get("id") or employment_data.get("label")
                ),
                "workplace_type": workplace_type,
                "posted_at": post.get("createdOn"),
                "salary": _salary_text(description),
            }
            fields["source"] = "Rippling"
            yield {
                key: value for key, value in fields.items()
                if value is not None
            }

        page_number += 1
        total_pages = listing.get("totalPages")
        if total_pages is not None:
            if page_number >= int(total_pages):
                break
        elif len(items) < int(listing.get("pageSize") or 1000):
            break
