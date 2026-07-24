
import re
import html
from urllib.parse import urljoin

SITE = "https://zuplo.com/careers.md"
EXTRA_HOSTS = []

COMPANY = "Zuplo"


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def _parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _strip_quotes(value)
    return meta, text[match.end():]


def _markdown_to_text(markdown):
    text = re.sub(r"<[^>\n]+/?>", " ", markdown)
    fence = "`" * 3
    text = re.sub(re.escape(fence) + r".*?" + re.escape(fence), " ", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.M)
    text = re.sub(r"[*_]{1,3}", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _job(**data):
    return {k: v for k, v in data.items() if v is not None and v != ""}


def _open_position_rows(markdown):
    in_section = False
    for line in markdown.splitlines():
        if re.match(r"^##\s+Open Positions\s*$", line):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        match = re.match(
            r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
            line,
        )
        if not match:
            continue
        title, path, location, employment_type = match.groups()
        yield title.strip(), path.strip(), location.strip(), employment_type.strip()


def crawl(ctx):
    board = ctx.fetch(SITE)
    for row_title, path, row_location, row_employment_type in _open_position_rows(board):
        detail_md_url = urljoin(SITE, path.lstrip("/") + ".md")
        raw = ctx.fetch(detail_md_url)
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title") or row_title
        location = meta.get("location") or row_location
        employment_type = meta.get("employmentType") or row_employment_type
        canonical_url = meta.get("canonicalUrl") or urljoin(SITE, path)
        description = _markdown_to_text(body)
        yield _job(
            url=canonical_url,
            title=title,
            company=COMPANY,
            location=location,
            locations=[location] if location else None,
            description=description,
            employment_type=employment_type,
            workplace_type="Remote" if location and "remote" in location.lower() else None,
            external_id=path.rstrip("/").rsplit("/", 1)[-1],
            apply_url=canonical_url,
            source=SITE,
        )
