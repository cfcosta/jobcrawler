import json
import re

SITE = "https://preset.io/careers/"
EXTRA_HOSTS = []

_LISTING_URL = "https://preset.io/page-data/careers/page-data.json"
_BLOCK_TAGS = {"address", "article", "blockquote", "div", "h1", "h2", "h3", "h4", "h5", "h6", "p", "section"}


def _render_node(node):
    if not isinstance(node, dict):
        return ""

    if node.get("type") == "text":
        return node.get("value") or ""

    tag = str(node.get("tagName") or "").lower()
    if tag in {"svg", "script", "style"}:
        return ""

    content = "".join(_render_node(child) for child in node.get("children", []))
    if tag == "li":
        return "\n- " + content.strip() + "\n"
    if tag == "br":
        return "\n"
    if tag in _BLOCK_TAGS or tag in {"ul", "ol"}:
        return "\n" + content.strip() + "\n"
    return content


def _description(ast):
    raw = _render_node(ast)
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip()


def crawl(ctx):
    listing = json.loads(ctx.fetch(_LISTING_URL))
    nodes = listing["result"]["data"]["allMarkdownRemark"]["nodes"]

    for node in nodes:
        frontmatter = node.get("frontmatter") or {}
        slug = str(frontmatter.get("slug") or "").strip("/")
        if not re.fullmatch(r"[A-Za-z0-9-]+", slug):
            continue

        url = f"https://preset.io/careers/{slug}/"
        detail_url = f"https://preset.io/page-data/careers/{slug}/page-data.json"
        detail = json.loads(ctx.fetch(detail_url))
        markdown = detail["result"]["data"]["markdownRemark"]
        detail_frontmatter = markdown.get("frontmatter") or frontmatter

        fields = {
            "url": url,
            "title": detail_frontmatter.get("title"),
            "company": "Preset",
            "location": detail_frontmatter.get("location"),
            "description": _description(markdown.get("htmlAst") or {}),
            "department": detail_frontmatter.get("department"),
            "employment_type": detail_frontmatter.get("type"),
            "external_id": node.get("id"),
            "source": SITE,
        }
        yield {key: value for key, value in fields.items() if value is not None}
