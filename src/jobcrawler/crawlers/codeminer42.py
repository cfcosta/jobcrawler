
import re
from html import unescape
from urllib.parse import urljoin, urlparse


SITE = "https://www.codeminer42.com/careers"
EXTRA_HOSTS = []

COMPANY = "Codeminer42"


def _attribute(tag, name):
    match = re.search(
        r'\b' + re.escape(name) + r'\s*=\s*(["\'])(.*?)\1',
        tag,
        re.IGNORECASE | re.DOTALL,
    )
    return unescape(match.group(2)).strip() if match else None


def _text(fragment):
    if not fragment:
        return None

    fragment = re.sub(
        r'<(?:br|/p|/li|/h[1-6]|/ul|/ol|/div|/section)\b[^>]*>',
        "\n",
        fragment,
        flags=re.IGNORECASE,
    )
    fragment = re.sub(r'<[^>]+>', ' ', fragment)
    fragment = unescape(fragment)

    lines = []
    for raw_line in fragment.splitlines():
        line = " ".join(raw_line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    listing_html = ctx.fetch(SITE)
    site_host = urlparse(SITE).hostname
    urls = []
    seen = set()

    for anchor in re.findall(r'<a\b[^>]*>', listing_html, re.IGNORECASE):
        href = _attribute(anchor, "href")
        label = _attribute(anchor, "aria-label")
        if not href or not label or label.casefold() != "apply":
            continue

        absolute = urljoin(SITE, href)
        parsed = urlparse(absolute)
        path = parsed.path.rstrip("/")
        if parsed.hostname != site_host or not path.startswith("/careers/"):
            continue

        url = absolute.split("#", 1)[0].split("?", 1)[0]
        if url not in seen:
            seen.add(url)
            urls.append(url)

    for url in urls:
        page = ctx.fetch(url)

        title_match = re.search(
            r'<h1\b[^>]*>(.*?)</h1>',
            page,
            re.IGNORECASE | re.DOTALL,
        )
        location_match = re.search(
            r'</h1>\s*<p\b[^>]*>(.*?)</p>',
            page,
            re.IGNORECASE | re.DOTALL,
        )
        description_match = re.search(
            r'<section\b(?=[^>]*\bclass\s*=\s*(["\'])[^"\']*\bmarkdown\b[^"\']*\1)[^>]*>'
            r'(.*?)</section>',
            page,
            re.IGNORECASE | re.DOTALL,
        )

        title = _text(title_match.group(1)) if title_match else None
        location = _text(location_match.group(1)) if location_match else None
        description = _text(description_match.group(2)) if description_match else None

        if not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }
        if location.casefold() == "remote":
            fields["workplace_type"] = "remote"

        yield fields
