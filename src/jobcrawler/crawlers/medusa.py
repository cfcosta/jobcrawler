
import json
import re
from urllib.parse import quote



SITE = "https://medusajs.com/careers"
EXTRA_HOSTS = []


def _body_text(response):
    body = response
    if not isinstance(response, (str, bytes)):
        body = response.body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _next_data(response):
    document = _body_text(response)
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError("Page does not contain Next.js data")
    return json.loads(match.group(1))


def _page(data):
    return data["props"]["pageProps"]["initial"]["data"]["page"]


def _portable_text(blocks):
    paragraphs = []
    for block in blocks or []:
        if block.get("_type") != "block":
            continue

        text = "".join(
            child.get("text", "")
            for child in block.get("children", [])
            if child.get("_type") == "span"
        ).strip()
        if not text:
            continue

        if block.get("listItem"):
            text = "- " + text
        paragraphs.append(text)

    return "\n\n".join(paragraphs)


def crawl(ctx):
    listing_response = ctx.fetch(SITE)
    careers = _page(_next_data(listing_response)).get("careers", [])

    seen = set()
    for role in careers:
        slug = role.get("slug")
        if not slug:
            continue

        slug = str(slug).strip("/")
        if not slug or slug in seen:
            continue
        seen.add(slug)

        encoded_slug = quote(slug, safe="-._~")
        job_url = SITE.rstrip("/") + "/" + encoded_slug + "/"
        detail_response = ctx.fetch(job_url)
        detail = _page(_next_data(detail_response))
        description = _portable_text(detail.get("content"))

        title = detail.get("title") or role.get("title")
        location = detail.get("location") or role.get("location")
        if not title or not location or not description:
            continue

        yield {
            "url": job_url,
            "title": title,
            "company": "Medusa",
            "location": location,
            "description": description,
            "source": SITE,
            "department": detail.get("team") or role.get("team"),
            "employment_type": detail.get("time") or role.get("time"),
            "external_id": slug,
            "apply_url": job_url,
        }
