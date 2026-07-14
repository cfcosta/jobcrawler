
import html
import re
from urllib.parse import urljoin, urlparse




SITE = "https://sematext.com/jobs/"
EXTRA_HOSTS = []


def clean_text(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(?:p|li|h[1-6]|ul|ol|div|section|blockquote|tr)\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<li\b[^>]*>", "- ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value, flags=re.S)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    listing_html = ctx.fetch(SITE)
    section_match = re.search(
        r'<section\b[^>]*\bid=["\']open-positions["\'][^>]*>(.*?)</section>',
        listing_html,
        re.I | re.S,
    )
    if not section_match:
        return

    cards = re.findall(
        r'<a\b(?=[^>]*\bclass=["\'][^"\']*\bpost-card\b[^"\']*\bis__job\b[^"\']*["\'])'
        r'[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        section_match.group(1),
        re.I | re.S,
    )

    seen = set()
    for href, card_html in cards:
        title_match = re.search(
            r'<h3\b[^>]*\bclass=["\'][^"\']*\btext-size-h4\b[^"\']*["\'][^>]*>(.*?)</h3>',
            card_html,
            re.I | re.S,
        )
        if not title_match:
            continue

        url = urljoin(SITE, html.unescape(href))
        parsed = urlparse(url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname != "sematext.com"
            or not re.match(r"^/(?:job|jobs)/[^/]+/?$", parsed.path)
            or url in seen
        ):
            continue
        seen.add(url)

        title = clean_text(title_match.group(1))
        detail_html = ctx.fetch(url)

        content_match = re.search(
            r'<div\b[^>]*\bclass=["\'][^"\']*\bbrxe-post-content\b[^"\']*["\'][^>]*>'
            r'(.*?)(?=<div\b[^>]*\bclass=["\'][^"\']*\bbutton-container\b|</main>)',
            detail_html,
            re.I | re.S,
        )
        if not content_match:
            continue
        body_html = re.sub(r"<!--.*?-->", "", content_match.group(1), flags=re.S)

        apply_match = re.search(
            r'<a\b[^>]*\bhref=["\'](mailto:[^"\']+)["\'][^>]*>',
            body_html,
            re.I,
        )

        body_html = re.sub(r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>", "", body_html, flags=re.I | re.S)
        body_html = re.sub(
            r'<div\b[^>]*style=["\'][^"\']*position\s*:\s*absolute[^"\']*left\s*:\s*-\d+[^"\']*["\'][^>]*>.*?</div>',
            "",
            body_html,
            flags=re.I | re.S,
        )
        body_html = re.sub(
            r'<p\b[^>]*class=["\'][^"\']*\bjob-apply\b[^"\']*["\'][^>]*>.*?</p>',
            "",
            body_html,
            flags=re.I | re.S,
        )
        body_html = re.sub(
            r'<p\b[^>]*class=["\'][^"\']*\bspace-top\b[^"\']*["\'][^>]*>.*',
            "",
            body_html,
            flags=re.I | re.S,
        )

        description = clean_text(body_html)
        location_match = re.search(
            r'<h[1-6]\b[^>]*>\s*Location\s*</h[1-6]>\s*'
            r'(.*?)(?=<h[1-6]\b|<p\b[^>]*class=["\'][^"\']*\bjob-apply\b|'
            r'<a\b[^>]*(?:mailto:|Apply)|<p\b[^>]*class=["\'][^"\']*\bspace-top\b|\Z)',
            body_html,
            re.I | re.S,
        )
        location = clean_text(location_match.group(1)) if location_match else None
        if location:
            location = re.sub(r"\s+", " ", location).strip()

        if not title or not description or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Sematext",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if apply_match:
            fields["apply_url"] = html.unescape(apply_match.group(1))

        yield fields
