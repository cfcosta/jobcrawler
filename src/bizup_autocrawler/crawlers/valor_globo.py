import re

SITE = "https://valor.globo.com/"
FEED_URL = "https://valor.globo.com/rss/valor"

ARTICLE_RE = re.compile(
    r"^https?://[^/]*valor\.globo\.com/[^/]+/noticia/\d{4}/\d{2}/\d{2}/.+\.ghtml$"
)


def _strip_tags(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</\s*(p|div|li|h[1-6])\s*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_title(html):
    m = re.search(
        r"<\s*meta\s+property=[\"']og:title[\"']\s+content=[\"']([^\"']*)[\"']",
        html,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"<\s*title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""


def _extract_body(html):
    for pat in [
        r'<article\b[^>]*class="[^"]*mc-body[^"]*"[^>]*>(.*?)</article>',
        r"<article\b[^>]*>(.*?)</article>",
        r'<div\b[^>]*class="[^"]*mc-body[^"]*"[^>]*>(.*?)</div>\s*(?:<!--\s*rec|<footer)',
        r'<div\b[^>]*class="[^"]*materia-conteudo[^"]*"[^>]*>(.*?)</div>',
        r'<div\b[^>]*class="[^"]*content-text[^"]*"[^>]*>(.*?)</div>',
        r'<div\b[^>]*class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>',
    ]:
        m = re.search(pat, html, re.S | re.I)
        if m:
            text = _strip_tags(m.group(1))
            if len(text) > 200:
                return text
    ps = re.findall(r"<\s*p\b[^>]*>(.*?)</\s*p\s*>", html, re.S | re.I)
    if ps:
        text = "\n\n".join(_strip_tags(p) for p in ps)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        if len(text) > 200:
            return text
    return ""


def _parse_feed(feed_text):
    urls = []
    for m in re.finditer(r"<item\b.*?</item>", feed_text, re.S | re.I):
        item = m.group(0)
        lm = re.search(r"<link>(.*?)</link>", item, re.S)
        if lm:
            link = lm.group(1).strip()
            if ARTICLE_RE.match(link):
                urls.append(link)
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def crawl(ctx):
    feed_text = ctx.fetch(FEED_URL)
    urls = _parse_feed(feed_text)
    for url in urls:
        try:
            html = ctx.fetch(url)
        except Exception:
            continue
        title = _extract_title(html)
        body = _extract_body(html)
        if not title or not body:
            continue
        yield {"url": url, "title": title, "body": body, "source": SITE}
