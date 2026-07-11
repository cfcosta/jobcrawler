
import json
import re
import html

SITE = "https://traefik.io/careers"
EXTRA_HOSTS = []

COMPANY = "Traefik Labs"

def _text_from_html(markup):
    text = markup or ""
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|section|article|header|footer|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _next_data(page):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', page, re.S)
    if not match:
        return {}
    return json.loads(html.unescape(match.group(1)))

def _highlight_values(markup):
    match = re.search(r'<div class=["\']job-highlights["\']>(.*?)<!--kg-card-end:\s*html-->', markup or "", re.S)
    if not match:
        return []

    values = []
    for item in re.finditer(r'<div class=["\'](?:location|type|department)["\']>(.*?)</div>', match.group(1), re.S):
        text = _text_from_html(item.group(1))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            values.append(lines[-1])

    if values:
        return values

    text = _text_from_html(match.group(1))
    return [line.strip() for line in text.splitlines() if line.strip()]

def _without_empty(job):
    clean = {}
    for key, value in job.items():
        if value is not None and value != "" and value != []:
            clean[key] = value
    return clean

def crawl(ctx):
    page = ctx.fetch(SITE)
    data = _next_data(page)
    posts = data.get("props", {}).get("pageProps", {}).get("data", {}).get("job_posts", [])

    for post in posts:
        title = (post.get("title") or "").strip()
        slug = (post.get("slug") or "").strip("/")
        body_html = post.get("html") or ""
        highlights = _highlight_values(body_html)

        location = highlights[0] if highlights else "Worldwide"
        url = SITE.rstrip("/") + "/" + slug

        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "locations": [location],
            "description": _text_from_html(body_html),
            "posted_at": post.get("published_at"),
            "external_id": post.get("id"),
            "apply_url": url,
            "source": SITE,
        }

        if len(highlights) > 1:
            second = highlights[1]
            if second.lower() == "remote":
                job["workplace_type"] = second
            else:
                job["employment_type"] = second

        if len(highlights) > 2 and not highlights[2].lower().startswith("reports to"):
            job["department"] = highlights[2]

        yield _without_empty(job)
