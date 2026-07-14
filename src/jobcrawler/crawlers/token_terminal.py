
import html
import json
import re
from urllib.parse import quote

SITE = "https://tokenterminal.com/careers"
EXTRA_HOSTS = []


def _next_data(page):
    match = re.search(
        r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None

    payload = match.group(1).strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return json.loads(html.unescape(payload))


def _posting_html(page):
    opening = re.search(
        r'<div\b[^>]*\bclass=["\'][^"\']*\bprose\b[^"\']*["\'][^>]*>',
        page,
        re.IGNORECASE,
    )
    if not opening:
        return None

    depth = 1
    for tag in re.finditer(r'</?div\b[^>]*>', page[opening.end():], re.IGNORECASE):
        if tag.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return page[opening.end():opening.end() + tag.start()]
        else:
            depth += 1
    return None


def _plain_text(fragment):
    text = re.sub(r'<br\s*/?>', '\n', fragment, flags=re.IGNORECASE)
    text = re.sub(r'<li\b[^>]*>', '\n- ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(
        r'</?(?:p|h[1-6]|ul|ol)\b[^>]*>',
        '\n',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    listing_page = ctx.fetch(SITE)
    listing_data = _next_data(listing_page)
    if not listing_data:
        return

    page_props = listing_data.get("props", {}).get("pageProps", {})
    container = page_props.get("jobPostingsByTeam") or {}
    postings = container.get("Data") or [] if isinstance(container, dict) else []

    seen = set()
    for listing_job in postings:
        if not isinstance(listing_job, dict) or listing_job.get("active") is not True:
            continue

        slug = listing_job.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue

        slug = slug.strip().strip("/")
        url = SITE + "/" + quote(slug, safe="/-._~")
        if url in seen:
            continue
        seen.add(url)

        detail_page = ctx.fetch(url)
        detail_data = _next_data(detail_page) or {}
        job = detail_data.get("props", {}).get("pageProps", {}).get("jobPosting") or {}
        if not isinstance(job, dict) or job.get("active") is not True:
            continue

        body_html = _posting_html(detail_page)
        description = _plain_text(body_html) if body_html else None
        title = job.get("title") or listing_job.get("title")
        location = job.get("location") or listing_job.get("location")
        if not title or not location or not description:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Token Terminal",
            "location": location,
            "description": description,
            "source": SITE,
        }

        employment_type = job.get("type") or listing_job.get("type")
        department = job.get("team") or listing_job.get("team")
        posted_at = job.get("publishedOn") or listing_job.get("publishedOn")
        apply_match = re.search(
            r'href=["\'](mailto:[^"\']+)["\']',
            body_html,
            re.IGNORECASE,
        )

        if employment_type:
            item["employment_type"] = employment_type
        if department:
            item["department"] = department
        if posted_at:
            item["posted_at"] = posted_at
        if apply_match:
            item["apply_url"] = html.unescape(apply_match.group(1))

        yield item
