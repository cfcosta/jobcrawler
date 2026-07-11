
SITE = "https://www.dragonflydb.io/api/careers?limit=100&depth=1"
EXTRA_HOSTS = []

import json
import re

COMPANY = "Dragonfly"
DETAIL_BASE = "https://www.dragonflydb.io/careers/"

_BLOCK_TYPES = {
    "paragraph", "heading", "listitem", "quote", "code", "upload", "horizontalrule"
}


def _clean_space(text):
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _node_text(node):
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return node.get("text") or ""
    if node_type == "linebreak":
        return "\n"

    pieces = []
    for child in node.get("children") or []:
        text = _node_text(child)
        if text:
            pieces.append(text)

    if node_type == "list":
        return "\n".join(part for part in pieces if part.strip())

    text = "".join(pieces)
    if node_type == "listitem":
        text = _clean_space(text)
        return ("- " + text) if text else ""

    return text


def _rich_text_to_plain(content):
    root = content.get("root") if isinstance(content, dict) else None
    children = root.get("children") if isinstance(root, dict) else []
    blocks = []

    for child in children:
        if not isinstance(child, dict):
            continue
        text = _node_text(child)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            blocks.append(text)

    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(blocks)).strip()


def _job_url(doc):
    slug = (doc.get("slug") or "").strip("/")
    return DETAIL_BASE + slug if slug else SITE


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for doc in data.get("docs") or []:
        if doc.get("_status") and doc.get("_status") != "published":
            continue

        title = (doc.get("title") or "").strip()
        location = (doc.get("location") or "").strip()
        description = _rich_text_to_plain(doc.get("content") or {})
        if not description:
            description = (doc.get("summary") or "").strip()

        if not title or not location or not description:
            continue

        job = {
            "url": _job_url(doc),
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "external_id": str(doc.get("id")),
            "source": SITE,
        }

        employment_type = (doc.get("type") or "").strip()
        if employment_type:
            job["employment_type"] = employment_type

        department = (doc.get("category") or "").strip()
        if department:
            job["department"] = department

        posted_at = (doc.get("publishedAt") or doc.get("createdAt") or "").strip()
        if posted_at:
            job["posted_at"] = posted_at

        yield job
