import re
import json
from urllib.parse import urlparse

SITE = "https://braziljournal.com/"

API_URL = "https://braziljournal.com/wp-json/wp/v2/posts"

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("&#038;", "&").replace("&#8217;", "\u2019")
    text = text.replace("&#8220;", "\u201c").replace("&#8221;", "\u201d")
    text = text.replace("&#8211;", "\u2013").replace("&#8212;", "\u2014")
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """Yield article items from the Brazil Journal WP REST API."""
    page = 1
    per_page = 50
    max_pages = 5
    while page <= max_pages:
        url = f"{API_URL}?per_page={per_page}&page={page}"
        try:
            resp = ctx.fetch(url)
        except Exception:
            break
        try:
            posts = json.loads(resp)
        except Exception:
            break
        if not isinstance(posts, list) or len(posts) == 0:
            break
        for post in posts:
            link = post.get("link", "")
            title_html = post.get("title", {}).get("rendered", "")
            content_html = post.get("content", {}).get("rendered", "")
            title = _strip_html(title_html)
            body = _strip_html(content_html)
            if not title or not body or not link:
                continue
            parsed = urlparse(link)
            if "braziljournal.com" not in parsed.netloc:
                continue
            yield {
                "url": link,
                "title": title,
                "body": body,
                "source": link,
            }
        if len(posts) < per_page:
            break
        page += 1
