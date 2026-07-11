
import re
import html
from urllib.parse import urljoin

SITE = "https://www.armosec.io/careers/"
EXTRA_HOSTS = []


def _clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r"\s*\n\s*", "\n", value)
    return value.strip()


def _strip_tags(fragment):
    fragment = fragment or ""
    fragment = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h[1-6])\s*>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _first(pattern, text):
    match = re.search(pattern, text or "", re.I | re.S)
    if match:
        return match.group(1)
    return ""


def _position_links(board_html):
    links = []
    for anchor in re.findall(r'<a\b[^>]*class=["\'][^"\']*\bposition_link\b[^"\']*["\'][^>]*>.*?</a>', board_html, re.I | re.S):
        href = _first(r'href=["\']([^"\']+)["\']', anchor)
        title_html = _first(r'<p\b[^>]*class=["\'][^"\']*\bposition-title\b[^"\']*["\'][^>]*>(.*?)</p>', anchor)
        title = _clean_text(_strip_tags(title_html))
        if href and title:
            links.append((href, title))
    return links


def _extract_description_and_location(page_html):
    main = _first(
        r'<div\b[^>]*class=["\'][^"\']*\bpositions-wrapper\b[^"\']*["\'][^>]*>(.*?)<div\b[^>]*class=["\'][^"\']*\bform_wrapper\b',
        page_html,
    )
    if not main:
        main = _first(r'<section\b[^>]*class=["\'][^"\']*\bapplication_form_wrapper\b[^"\']*["\'][^>]*>(.*?)</section>', page_html)
    if not main:
        main = page_html

    location = None
    loc_html = _first(r'<p\b[^>]*class=["\'][^"\']*\blist-title\b[^"\']*["\'][^>]*>\s*Location:\s*(.*?)</p>', main)
    if loc_html:
        location = _clean_text(_strip_tags(loc_html))

    description = _strip_tags(main)
    description = re.sub(r"(?im)^Location:\s*.*$", "", description)
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    return description, location


def crawl(ctx):
    board_html = ctx.fetch(SITE)
    seen = set()

    for href, title in _position_links(board_html):
        url = urljoin(SITE, href)
        if url in seen:
            continue
        seen.add(url)

        page_html = ctx.fetch(url)
        description, location = _extract_description_and_location(page_html)

        yield {
            "url": url,
            "title": title,
            "company": "ARMO",
            "location": location or "Israel",
            "description": description,
            "source": SITE,
        }
