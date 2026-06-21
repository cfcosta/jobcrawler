SITE = "https://valor.globo.com/"
FEED_URL = "https://valor.globo.com/rss/valor"

import re
import xml.etree.ElementTree as ET


def _strip_html(text):
    """Remove HTML tags and normalize whitespace."""
    if not text:
        return ""
    text = re.sub(r"<img[^>]*>", "", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def crawl(ctx):
    """Yield article items discovered from the Valor RSS feed."""
    feed_xml = ctx.fetch(FEED_URL)
    root = ET.fromstring(feed_xml)
    channel = root.find("channel")
    if channel is None:
        return
    items = channel.findall("item")

    atom_ns = "{http://www.w3.org/2005/Atom}"
    seen = set()

    for item in items:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        if link in seen:
            continue
        seen.add(link)

        desc = item.findtext("description") or ""
        subtitle_el = item.find(f"{atom_ns}subtitle")
        subtitle = subtitle_el.text if subtitle_el is not None else ""

        body_parts = []
        if subtitle:
            sub_clean = _strip_html(subtitle)
            if sub_clean:
                body_parts.append(sub_clean)
        desc_clean = _strip_html(desc)
        if desc_clean:
            body_parts.append(desc_clean)

        body = "\n\n".join(body_parts).strip()
        if not body:
            continue

        yield {"url": link, "title": title, "body": body, "source": SITE}
