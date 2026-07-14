import html
import re


SITE = "https://www.cubbit.io/careers"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    fragment = re.sub(
        r"(?i)</?(?:p|div|ul|ol|h1|h2|h3|h4|section|article)\b[^>]*>",
        "\n",
        fragment,
    )
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = html.unescape(fragment).replace("\u200d", "")
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()


def crawl(ctx):
    sitemap = ctx.fetch("https://www.cubbit.io/sitemap.xml")
    urls = re.findall(
        r"<loc>\s*(https://www\.cubbit\.io/careers/[^<]+)\s*</loc>",
        sitemap,
        re.I,
    )

    seen = set()
    for encoded_url in urls:
        url = html.unescape(encoded_url).strip()
        if url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        title_match = re.search(
            r'<h1\b[^>]*id="jobTitle"[^>]*>(.*?)</h1>', page, re.I | re.S
        )
        body_match = re.search(
            r'<div\b[^>]*class="[^"]*\bjobs-richtext\b[^"]*"[^>]*>'
            r'(.*?)'
            r'<div\b[^>]*class="[^"]*\bjob-info-wrap\b',
            page,
            re.I | re.S,
        )
        location_match = re.search(
            r'<div\b[^>]*class="[^"]*\bjob-info-sticky\b[^"]*"[^>]*>'
            r'\s*<div\b[^>]*class="[^"]*\bbody-18\b[^"]*"[^>]*>'
            r'\s*Work location:\s*</div>'
            r'.*?<p\b[^>]*class="[^"]*\bu-bold\b[^"]*"[^>]*>(.*?)</p>',
            page,
            re.I | re.S,
        )

        title = _text(title_match.group(1)) if title_match else ""
        description = _text(body_match.group(1)) if body_match else ""
        location = _text(location_match.group(1)) if location_match else ""
        if not title or not description or not location:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Cubbit",
            "location": location,
            "description": description,
            "source": SITE,
        }

        compensation_match = re.search(
            r'<div\b[^>]*class="[^"]*\bjob-info-sticky\b[^"]*"[^>]*>'
            r'\s*<div\b[^>]*class="[^"]*\bbody-18\b[^"]*"[^>]*>'
            r'\s*Compensation:\s*</div>'
            r'.*?<p\b[^>]*class="[^"]*\bu-bold\b[^"]*"[^>]*>(.*?)</p>',
            page,
            re.I | re.S,
        )
        if compensation_match:
            compensation = _text(compensation_match.group(1))
            if compensation:
                fields["salary"] = compensation

        yield fields