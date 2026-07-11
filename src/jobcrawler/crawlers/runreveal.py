
import html
import re
from urllib.parse import urljoin, urlparse

SITE = "https://runreveal.com/careers"
EXTRA_HOSTS = []

COMPANY = "RunReveal"
SITEMAP_URL = "https://runreveal.com/sitemap.xml"


def _fetch(ctx, url):
    return ctx.fetch(url)


def _strip_tags(fragment):
    fragment = re.sub(r"<(script|style|svg)\b[^>]*>.*?</\1>", " ", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"</(p|div|li|h[1-6]|section|article|main)>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    text = re.sub(r"\xa0", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first_text(pattern, source):
    match = re.search(pattern, source, flags=re.I | re.S)
    if not match:
        return None
    text = _strip_tags(match.group(1))
    return text or None


def _career_urls_from_sitemap(xml):
    urls = []
    seen = set()
    for raw in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, flags=re.I | re.S):
        url = html.unescape(raw.strip())
        parsed = urlparse(url)
        if parsed.netloc != "runreveal.com":
            continue
        path = parsed.path.rstrip("/")
        if not path.startswith("/careers/"):
            continue
        # The careers landing page and non-detail anchors are not postings.
        if path == "/careers":
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _description(page_text, title, location):
    body_match = re.search(r"<body[^>]*>(.*?)</body>", page_text, flags=re.I | re.S)
    text = _strip_tags(body_match.group(1) if body_match else page_text)

    start = text.find("About RunReveal")
    if start < 0:
        return None
    text = text[start:]

    end_candidates = [
        text.find("One platform. All your security data."),
        text.find("Request a Demo"),
        text.find("Discord GitHub LinkedIn"),
    ]
    ends = [idx for idx in end_candidates if idx > 0]
    if ends:
        text = text[:min(ends)]

    parts = [title]
    if location:
        parts.append(location)
    parts.append(text.strip())
    return "\n\n".join(part for part in parts if part).strip()


def _posting_from_page(url, page_text):
    title = _first_text(r"<h1[^>]*>(.*?)</h1>", page_text)
    location = _first_text(r"<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>", page_text)
    if not title or not location:
        return None

    desc = _description(page_text, title, location)
    if not desc or "About RunReveal" not in desc or "To apply" not in desc:
        return None

    return {
        "url": url,
        "title": title,
        "company": COMPANY,
        "location": location,
        "description": desc,
        "source": SITE,
    }


def crawl(ctx):
    sitemap = _fetch(ctx, SITEMAP_URL)
    for url in _career_urls_from_sitemap(sitemap):
        page = _fetch(ctx, url)
        job = _posting_from_page(url, page)
        if job:
            yield job
