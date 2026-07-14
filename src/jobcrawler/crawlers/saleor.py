
import re
from urllib.parse import urljoin, urlparse

SITE = "https://saleor.notion.site/open-positions"
EXTRA_HOSTS = []

ROLE_PATH = re.compile(r"^/[0-9a-fA-F]{32}/?$")
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(
    r"<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)>",
    re.I | re.S,
)
HREF = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"']", re.I)
TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.I | re.S)
OG_TITLE = re.compile(
    r"<meta\b[^>]*\bproperty=[\"']og:title[\"'][^>]*\bcontent=[\"']([^\"']+)",
    re.I,
)
HEADING = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.I | re.S)


def clean_fragment(value):
    value = TAG.sub(" ", value)
    value = (
        value.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"\s+", " ", value).strip()


def text_lines(source):
    source = SCRIPT.sub(" ", source)
    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.I)
    source = re.sub(
        r"</(?:p|div|li|h1|h2|h3|h4|tr|section|article)>",
        "\n",
        source,
        flags=re.I,
    )
    source = TAG.sub(" ", source)
    source = (
        source.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
    )

    lines = []
    for raw in source.splitlines():
        value = re.sub(r"\s+", " ", raw).strip()
        if value:
            lines.append(value)
    return lines


def role_url(href):
    url = urljoin(SITE, href)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname != "saleor.notion.site":
        return None
    if not ROLE_PATH.fullmatch(parsed.path):
        return None
    return "https://saleor.notion.site/" + parsed.path.strip("/")


def find_value(lines, labels):
    for index, line in enumerate(lines):
        folded = line.casefold().rstrip(":").strip()
        for label in labels:
            key = label.casefold()
            if folded == key and index + 1 < len(lines):
                value = lines[index + 1].strip()
                if value:
                    return value
            if folded.startswith(key + ":"):
                value = line[len(label) + 1:].strip()
                if value:
                    return value
    return None


def page_title(source, lines):
    match = HEADING.search(source)
    if match:
        value = clean_fragment(match.group(1))
        if value:
            return value

    match = OG_TITLE.search(source)
    if match:
        value = clean_fragment(match.group(1))
        if value:
            return value

    match = TITLE.search(source)
    if match:
        value = clean_fragment(match.group(1))
        value = re.sub(r"\s+(?:[|]|[-])\s+(?:Notion|Saleor).*$", "", value, flags=re.I)
        if value and value.casefold() != "notion":
            return value

    return lines[0] if lines else None


def crawl(ctx):
    board_source = ctx.fetch(SITE)
    urls = []
    seen = set()

    for href in HREF.findall(board_source):
        url = role_url(href)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for url in urls:
        source = ctx.fetch(url)
        lines = text_lines(source)
        title = page_title(source, lines)
        if not title or not lines:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Saleor",
            "location": find_value(lines, ("Location",)) or "Not specified",
            "description": "\n".join(lines),
            "external_id": url.rsplit("/", 1)[-1].lower(),
        }

        department = find_value(lines, ("Department", "Team"))
        employment_type = find_value(
            lines,
            ("Employment type", "Employment-type", "Employment"),
        )
        if department:
            fields["department"] = department
        if employment_type:
            fields["employment_type"] = employment_type

        yield fields
