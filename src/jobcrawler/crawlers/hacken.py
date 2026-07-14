
import html
import re
from urllib.parse import urljoin

SITE = "https://hacken.io/careers/"
EXTRA_HOSTS = []


def _text(fragment):
    fragment = re.sub(
        r"<(?:script|style|svg)\b[^>]*>.*?</(?:script|style|svg)>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|"
        r"li|main|ol|p|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()


def _description(page):
    match = re.search(
        r'<div[^>]+class="[^"]*\bstyles_firstColumn__[^\s"]*[^"]*"[^>]*>'
        r'(.*?)'
        r'(?=<div[^>]+class="[^"]*\bstyles_secondColumn__[^\s"]*[^"]*")',
        page,
        re.I | re.S,
    )
    if match and "jobDescription" in match.group(1):
        return _text(match.group(1))

    for section in re.findall(r"<section\b[^>]*>.*?</section>", page, re.I | re.S):
        if re.search(r'class=["\']jobDescription["\']', section, re.I):
            section = re.split(
                r'<div[^>]+class="[^"]*\bstyles_secondColumn__[^\s"]*[^"]*"',
                section,
                maxsplit=1,
                flags=re.I,
            )[0]
            return _text(section)
    return None


def crawl(ctx):
    listing = ctx.fetch(SITE)
    seen = set()

    for row_match in re.finditer(r"<tr\b[^>]*>.*?</tr>", listing, re.I | re.S):
        row = row_match.group(0)
        link_match = re.search(
            r'href=["\'](/careers/(\d+)-[^"\']+/?)(?=["\'])',
            row,
            re.I,
        )
        if not link_match:
            continue

        path, external_id = link_match.groups()
        url = urljoin(SITE, path)
        if url in seen:
            continue
        seen.add(url)

        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)
        values = [_text(cell) for cell in cells]
        if len(values) < 4 or not all(values[:4]):
            continue

        title, department, employment_type, location = values[:4]
        description = _description(ctx.fetch(url))
        if not description:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Hacken",
            "location": location,
            "description": description,
            "employment_type": employment_type,
            "department": department,
            "external_id": external_id,
            "source": SITE,
        }
