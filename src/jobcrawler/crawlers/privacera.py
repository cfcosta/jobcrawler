
import html
import re

SITE = "https://privacera.com/careers/"
EXTRA_HOSTS = []

def _text(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()

def crawl(ctx):
    page = ctx.fetch(SITE)
    section_match = re.search(
        r'<section\b[^>]*\bid=["\']openings["\'][^>]*>(.*?)</section\s*>',
        page,
        re.I | re.S,
    )
    if not section_match:
        return

    role_pattern = re.compile(
        r'<h4\b[^>]*class=["\'][^"\']*\bwp-block-heading\b[^"\']*["\'][^>]*>'
        r'(.*?)</h4\s*>\s*'
        r'<p\b[^>]*>(.*?)</p\s*>.*?'
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>'
        r'\s*Download Full Description\s*</a\s*>',
        re.I | re.S,
    )

    for title_html, description_html, pdf_url in role_pattern.findall(
        section_match.group(1)
    ):
        title = _text(title_html)
        description = _text(description_html)
        url = html.unescape(pdf_url).strip()
        if not title or not description or not url:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Privacera",
            "location": "Not specified",
            "description": description,
            "source": SITE,
        }
