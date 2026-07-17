
import re
from html import unescape
from urllib.parse import urlparse

SITE = "https://www.mage.ai/company/careers"
EXTRA_HOSTS = []

SITEMAP_URL = "https://www.mage.ai/sitemap.xml"
CAREER_PATH = re.compile(r"^/company/careers/[^/?#]+$")
LOC_RE = re.compile(r"<loc\b[^>]*>(.*?)</loc>", re.I | re.S)
TITLE_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)
ARTICLE_RE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.I | re.S)
BLOCK_RE = re.compile(
    r"</?(?:h[1-6]|p|li|ul|ol|section|div|br)\b[^>]*>",
    re.I,
)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.I | re.S,
)


def _plain(fragment):
    fragment = SCRIPT_RE.sub(" ", fragment)
    fragment = BLOCK_RE.sub("\n", fragment)
    fragment = TAG_RE.sub(" ", fragment)
    fragment = unescape(fragment)

    lines = []
    for line in fragment.splitlines():
        value = " ".join(line.split())
        if value:
            lines.append(value)
    return "\n".join(lines)


def crawl(ctx):
    sitemap_html = ctx.fetch(SITEMAP_URL)
    seen = set()

    for raw_url in LOC_RE.findall(sitemap_html):
        discovered_url = unescape(TAG_RE.sub("", raw_url)).strip()
        path = urlparse(discovered_url).path
        if not CAREER_PATH.fullmatch(path):
            continue

        # Sitemap canonicals use mage.ai; normalize to SITE's authorized host.
        url = "https://www.mage.ai" + path
        if url in seen:
            continue
        seen.add(url)

        html = ctx.fetch(url)
        title_match = TITLE_RE.search(html)
        article_match = ARTICLE_RE.search(html)
        if not title_match or not article_match:
            continue

        title = _plain(title_match.group(1))
        description = _plain(article_match.group(1))
        if not title or not description:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Mage",
            "location": "Not specified",
            "description": description,
            "source": SITE,
        }
