import html
import re


SITE = "https://www.aklivity.io/careers"
EXTRA_HOSTS = []


def _plain_text(fragment):
    fragment = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:h[1-6]|p|li|ul|ol|div|section)>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)

    lines = []
    for line in fragment.splitlines():
        clean = " ".join(line.split())
        if clean and (not lines or clean != lines[-1]):
            lines.append(clean)
    return "\n".join(lines)


def _field(fragment, pattern):
    match = re.search(pattern, fragment, re.I | re.S)
    if not match:
        return None
    value = _plain_text(match.group(1))
    return value or None


def _listing_cards(page):
    pattern = (
        r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bjob-opening\b[^"\']*["\'])'
        r'(?=[^>]*\bhref=["\']([^"\']+)["\'])[^>]*>(.*?)</a>'
    )
    return re.findall(pattern, page, re.I | re.S)


def _job_body(page):
    start = re.search(
        r'<div\b[^>]*\bclass=["\'][^"\']*\bsection-wrapper\b'
        r'[^"\']*\bjob-description\b[^"\']*["\'][^>]*>',
        page,
        re.I,
    )
    if not start:
        return None

    token_pattern = re.compile(r'<div\b[^>]*>|</div\s*>', re.I)
    depth = 1
    end = None
    for token in token_pattern.finditer(page, start.end()):
        if token.group(0).lower().startswith("</"):
            depth -= 1
            if depth == 0:
                end = token.start()
                break
        else:
            depth += 1

    if end is None:
        return None
    return page[start.end():end]


def crawl(ctx):
    page = ctx.fetch(SITE)
    seen = set()

    for href, card in _listing_cards(page):
        title = _field(card, r'<h3\b[^>]*>(.*?)</h3>')
        location = _field(
            card,
            r'<div\b[^>]*\bclass=["\'][^"\']*\btc-secondary\b[^"\']*["\'][^>]*>'
            r'(.*?)</div>',
        )
        if not title or not location:
            continue

        if href.startswith("/"):
            url = "https://www.aklivity.io" + href
        elif href.startswith("https://www.aklivity.io/"):
            url = href
        else:
            continue

        if url in seen:
            continue
        seen.add(url)

        detail_page = ctx.fetch(url)
        body = _job_body(detail_page)
        if body is None:
            continue

        description = _plain_text(body)
        if not description:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Aklivity",
            "location": location,
            "description": description,
            "source": SITE,
        }
