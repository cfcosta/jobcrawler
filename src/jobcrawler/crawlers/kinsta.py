from html import unescape
import re
from urllib.parse import urljoin, urlparse

SITE = "https://kinsta.com/careers/"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(
        r"<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)\s*>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
        r"footer|h1|h2|h3|h4|h5|h6|header|hr|li|main|nav|ol|p|pre|section|"
        r"table|td|th|tr|ul)\b[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = unescape(re.sub(r"<[^>]+>", " ", fragment))
    lines = []
    for line in fragment.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _balanced_div(html, marker, start_at=0):
    marker_at = html.find(marker, start_at)
    if marker_at < 0:
        return None, -1

    start = html.rfind("<div", start_at, marker_at + 1)
    if start < 0:
        return None, -1

    depth = 0
    token_re = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)
    for match in token_re.finditer(html, start):
        if match.group(0).lower().startswith("<div"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return html[start:match.end()], match.end()
    return None, -1


def _opening_cards(html):
    offset = 0
    while True:
        card, end = _balanced_div(html, "job-opening", offset)
        if card is None:
            return
        yield card
        offset = end


def _listing_jobs(html):
    seen = set()
    for card in _opening_cards(html):
        title_match = re.search(
            r'<h2\b[^>]*class=["\'][^"\']*\bh4\b[^"\']*["\'][^>]*>'
            r'(.*?)</h2>',
            card,
            re.I | re.S,
        )
        link_match = re.search(
            r'<a\b[^>]*class=["\'][^"\']*\bbutton--secondary\b[^"\']*'
            r'["\'][^>]*href=["\']([^"\']+)["\']',
            card,
            re.I | re.S,
        )
        if not title_match or not link_match:
            continue

        title = _text(title_match.group(1))
        url = urljoin(SITE, unescape(link_match.group(1)))
        parsed = urlparse(url)
        if (
            not title
            or parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() != "kinsta.com"
            or not parsed.path.startswith("/careers/")
            or url in seen
        ):
            continue
        seen.add(url)
        yield title, url


def _metadata(card):
    result = {}
    dl_match = re.search(
        r'<dl\b[^>]*class=["\'][^"\']*\bjob-categories\b[^"\']*'
        r'["\'][^>]*>(.*?)</dl>',
        card,
        re.I | re.S,
    )
    if not dl_match:
        return result, None

    for key_html, value_html in re.findall(
        r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>",
        dl_match.group(1),
        re.I | re.S,
    ):
        key = _text(key_html).lower()
        value = _text(value_html)
        if key and value:
            result[key] = value
    return result, dl_match.end()


def _detail(html):
    card, unused = _balanced_div(html, 'id="job-post"')
    if card is None:
        card, unused = _balanced_div(html, "id='job-post'")
    if card is None:
        return None

    metadata, body_start = _metadata(card)
    if body_start is None:
        return None

    boundary = re.search(
        r'<div\b[^>]*class=["\'][^"\']*\bborder--top--gray\b',
        card[body_start:],
        re.I,
    )
    body_end = body_start + boundary.start() if boundary else len(card)
    description = _text(card[body_start:body_end])

    apply_match = re.search(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>\s*Apply(?:\s+Now!?)?\s*</a>',
        card,
        re.I | re.S,
    )
    apply_url = unescape(apply_match.group(1)).strip() if apply_match else None
    return metadata, description, apply_url


def crawl(ctx):
    listing = ctx.fetch(SITE)
    for title, url in _listing_jobs(listing):
        detail = _detail(ctx.fetch(url))
        if detail is None:
            continue

        metadata, description, apply_url = detail
        location = metadata.get("location")
        commitment = metadata.get("commitment")
        if not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Kinsta",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if apply_url:
            fields["apply_url"] = apply_url
        if commitment:
            if re.search(r"\bremote\b", commitment, re.I):
                fields["workplace_type"] = "Remote"
            employment_type = re.sub(
                r"(?:^|\s+)Remote(?:\s+|$)",
                " ",
                commitment,
                flags=re.I,
            ).strip()
            if employment_type:
                fields["employment_type"] = employment_type

        yield fields
