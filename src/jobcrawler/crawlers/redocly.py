
import json
import re

SITE = "https://redocly.com/careers"
EXTRA_HOSTS = []

_SITEMAP_URL = "https://redocly.com/sitemap.xml"
_JOB_URL_RE = re.compile(
    r"<loc>(https://redocly\.com/careers/([a-z0-9-]+))/?</loc>"
)


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    return response.text if hasattr(response, "text") else str(response)


def _node_text(node):
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_node_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    name = node.get("name", "")
    text = "".join(_node_text(child) for child in node.get("children", []))

    if name == "li":
        return "- " + text.strip() + "\n"
    if name in {"Heading", "p", "blockquote"}:
        return text.strip() + "\n"
    if name in {"ul", "ol", "article"}:
        return text + "\n"
    return text


def _description(ast):
    text = _node_text(ast)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    sitemap = _fetch_text(ctx, _SITEMAP_URL)
    postings = sorted(set(_JOB_URL_RE.findall(sitemap)))

    for url, slug in postings:
        data_url = (
            "https://redocly.com/page-data/careers/"
            + slug
            + "/data.json"
        )
        data = json.loads(_fetch_text(ctx, data_url))
        props = data.get("props") or {}
        frontmatter = props.get("frontmatter") or {}

        title = frontmatter.get("title")
        locations = [
            location
            for location in frontmatter.get("locations", [])
            if isinstance(location, str) and location.strip()
        ]
        description = _description(props.get("ast"))

        if not title or not locations or not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Redocly",
            "location": locations[0],
            "locations": locations,
            "description": description,
            "source": SITE,
        }

        employment_type = frontmatter.get("employmentType")
        if employment_type:
            job["employment_type"] = employment_type

        yield job
