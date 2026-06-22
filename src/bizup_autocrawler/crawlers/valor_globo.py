"""
Crawler for valor.globo.com — uses the RSS feed.
"""

import re

SITE = "https://valor.globo.com/"
FEED_URL = "https://valor.globo.com/rss/valor"


def _strip_html(text):
    """Remove HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_description(desc_text):
    """Extract article body from the <description> element.

    The feed embeds an image inside CDATA, then the actual article
    text follows after the CDATA close.  We strip the CDATA block
    and any remaining HTML tags.
    """
    # Remove CDATA blocks (usually just an <img> tag)
    text = re.sub(r"<!\[CDATA\[.*?\]\]>", "", desc_text, flags=re.DOTALL)
    # Strip remaining HTML tags
    text = _strip_html(text)
    return text


def crawl(ctx):
    """Yield article dicts with url, title, body, source from the RSS feed."""
    xml_text = ctx.fetch(FEED_URL)

    # Robust regex-based parse (the feed has namespaces that make
    # ElementTree awkward; regex is simpler and reliable here).
    items = re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL)

    for raw_item in items:
        title_m = re.search(r"<title>(.*?)</title>", raw_item, re.DOTALL)
        link_m = re.search(r"<link>(.*?)</link>", raw_item, re.DOTALL)
        desc_m = re.search(r"<description>(.*?)</description>", raw_item, re.DOTALL)

        if not (title_m and link_m and desc_m):
            continue

        title = _strip_html(title_m.group(1))
        url = link_m.group(1).strip()
        body = _parse_description(desc_m.group(1))

        if title and body and url.startswith(SITE):
            yield {
                "url": url,
                "title": title,
                "body": body,
                "source": SITE,
            }
