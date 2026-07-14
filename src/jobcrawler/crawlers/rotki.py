
import json
import re

SITE = "https://rotki.com/jobs/_payload.json"
EXTRA_HOSTS = []


def _deref(table, index):
    value = table[index]
    if isinstance(value, dict):
        return {key: _resolve(table, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(table, item) for item in value]
    return value


def _resolve(table, value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _deref(table, value)
    if isinstance(value, list):
        return [_resolve(table, item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(table, item) for key, item in value.items()}
    return value


def _render_node(node):
    if isinstance(node, str):
        return node
    if not isinstance(node, list):
        return ""

    if node and isinstance(node[0], str):
        tag = node[0].lower()
        children = node[2:] if len(node) > 1 and isinstance(node[1], dict) else node[1:]
        text = "".join(_render_node(child) for child in children)

        if tag == "li":
            return "- " + text.strip() + "\n"
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"}:
            return text.strip() + "\n\n"
        if tag in {"ul", "ol"}:
            return text.rstrip() + "\n\n"
        if tag in {"br", "hr"}:
            return "\n"
        return text

    return "".join(_render_node(child) for child in node)


def _clean_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_mailto(node):
    if isinstance(node, dict):
        href = node.get("href")
        if isinstance(href, str) and href.startswith("mailto:"):
            return href
        for value in node.values():
            found = _find_mailto(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_mailto(value)
            if found:
                return found
    return None


def crawl(ctx):
    table = json.loads(ctx.fetch(SITE))
    job_refs = table[table[2]["jobs"]]

    for ref in job_refs:
        job = _deref(table, ref)
        tags = job.get("tags") or []
        body = job["body"]["value"]

        fields = {
            "url": "https://rotki.com" + job["path"],
            "title": job["title"],
            "company": "rotki",
            "location": "Remote" if "remote" in tags else None,
            "description": _clean_text(_render_node(body)),
            "source": SITE,
            "workplace_type": "remote" if "remote" in tags else None,
            "department": job.get("category"),
            "external_id": job.get("__hash__"),
            "apply_url": _find_mailto(body),
        }

        employment_type = next(
            (
                tag
                for tag in tags
                if tag in {
                    "full-time",
                    "part-time",
                    "contract",
                    "temporary",
                    "internship",
                }
            ),
            None,
        )
        if employment_type:
            fields["employment_type"] = employment_type

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
