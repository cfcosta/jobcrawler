
import re
from html import unescape

SITE = "https://www.mage.ai/company/careers"
EXTRA_HOSTS = []


def clean_text(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return " ".join(unescape(fragment).split())


def detail_urls(page):
    hrefs = re.findall(
        r"<a\b[^>]*\bhref=[\"']\./careers/([a-z0-9-]+)(?:[?#][^\"']*)?[\"']",
        page,
        flags=re.I,
    )
    seen = set()
    for slug in hrefs:
        url = SITE + "/" + slug.lower()
        if url not in seen:
            seen.add(url)
            yield url


def parse_posting(page):
    blocks = []
    for match in re.finditer(
        r"<(h1|h2|h3|p)\b[^>]*>(.*?)</\1\s*>",
        page,
        flags=re.I | re.S,
    ):
        tag = match.group(1).lower()
        text = clean_text(match.group(2))
        if text:
            blocks.append((tag, text))

    title_indexes = [
        index
        for index, block in enumerate(blocks)
        if block[0] == "h1" and block[1]
    ]
    if not title_indexes:
        return None, None

    # Framer emits responsive copies; the last H1 immediately precedes the JD.
    start = title_indexes[-1]
    title = blocks[start][1]
    body = []

    for tag, text in blocks[start + 1:]:
        if tag == "h3" and text.casefold() == "open positions":
            break
        if tag in ("h2", "h3", "p"):
            body.append(text)

    return title, "\n\n".join(body).strip()


def crawl(ctx):
    listing = ctx.fetch(SITE)
    for url in detail_urls(listing):
        page = ctx.fetch(url)
        title, description = parse_posting(page)
        if not title or not description:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Mage AI",
            "location": "Not specified",
            "description": description,
            "source": SITE,
        }
