"""
Crawler for braziljournal.com using the WordPress REST API.
"""

import json
import re
import html
from urllib.parse import urlparse

SITE = "https://braziljournal.com/"

API_URL = "https://braziljournal.com/wp-json/wp/v2/posts"

MAX_PAGES = 2


def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)
    s = re.sub(
        r"(?i)</?(p|div|br|h[1-6]|li|ul|ol|blockquote|tr|td|th|hr)[^>]*>", "\n", s
    )
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\n{2,}", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _is_on_domain(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "braziljournal.com" or parsed.netloc.endswith(
        ".braziljournal.com"
    )


def crawl(ctx):
    page = 1
    per_page = 20
    seen = set()
    while page <= MAX_PAGES:
        url = f"{API_URL}?per_page={per_page}&page={page}"
        resp = ctx.fetch(url)
        try:
            posts = json.loads(resp)
        except Exception:
            break
        if not posts:
            break
        for post in posts:
            link = post.get("link", "")
            if not link or not _is_on_domain(link):
                continue
            if link in seen:
                continue
            seen.add(link)
            title = _strip_html(post.get("title", {}).get("rendered", ""))
            body = _strip_html(post.get("content", {}).get("rendered", ""))
            if title and body:
                yield {
                    "url": link,
                    "title": title,
                    "body": body,
                    "source": "braziljournal.com",
                }
        page += 1
