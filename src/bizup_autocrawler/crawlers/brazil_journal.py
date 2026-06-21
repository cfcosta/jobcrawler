import html
import json
import re

SITE = "https://braziljournal.com/"
API_URL = "https://braziljournal.com/wp-json/wp/v2/posts"
PER_PAGE = 100
MAX_PAGES = 3  # 3 paginated requests -> up to 300 recent posts, full bodies

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text):
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def crawl(ctx):
    # WordPress REST API: structured JSON, 100 posts/request, content.rendered is
    # the full article body (no scraping, no paywall here). Paginate until a page
    # comes back empty or errors (past the last page).
    for page in range(1, MAX_PAGES + 1):
        url = f"{API_URL}?per_page={PER_PAGE}&page={page}&_fields=link,title,content"
        try:
            posts = json.loads(ctx.fetch(url))
        except Exception:
            break
        if not isinstance(posts, list) or not posts:
            break
        for post in posts:
            link = post.get("link") or ""
            title = _strip_html((post.get("title") or {}).get("rendered") or "")
            body = _strip_html((post.get("content") or {}).get("rendered") or "")
            if link and title and body:
                yield {"url": link, "title": title, "body": body, "source": SITE}
