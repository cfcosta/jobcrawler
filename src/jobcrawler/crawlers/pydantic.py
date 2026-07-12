
import html
import json
import re

SITE = "https://pydantic.dev/jobs/llms.txt"
EXTRA_HOSTS = []

_ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]"
    r"\((?P<url>https://pydantic\.dev/jobs/[^)\s]+)\)"
    r" [—-] posted (?P<posted>\d{4}-\d{2}-\d{2})"
    r"\n[ \t]+(?P<department>[^\n]+)",
    re.MULTILINE,
)

_ISLAND_RE = re.compile(
    r"<astro-island\b(?P<attrs>[^>]*?)\bprops=\"(?P<props>[^\"]*)\"",
    re.DOTALL,
)

_BLOCK_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|"
    r"footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|"
    r"tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_SKIP_RE = re.compile(
    r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
    re.IGNORECASE | re.DOTALL,
)


def _to_text(value):
    value = _SKIP_RE.sub("", value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _description(page):
    for match in _ISLAND_RE.finditer(page):
        if "JobPost." not in match.group("attrs"):
            continue

        props = json.loads(html.unescape(match.group("props")))
        wrapped_content = props.get("content")
        if (
            not isinstance(wrapped_content, list)
            or len(wrapped_content) < 2
            or not isinstance(wrapped_content[1], str)
        ):
            raise ValueError("JobPost island has no rendered content")

        description = _to_text(wrapped_content[1])
        if not description:
            raise ValueError("JobPost island produced an empty description")
        return description

    raise ValueError("JobPost Astro island not found")


def crawl(ctx):
    index = ctx.fetch(SITE)
    seen = set()

    for match in _ENTRY_RE.finditer(index):
        url = match.group("url")
        if url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        yield {
            "url": url,
            "title": html.unescape(match.group("title")).strip(),
            "company": "Pydantic",
            "location": "Remote, PT (UTC-8) to CET (UTC+1)",
            "description": _description(page),
            "source": SITE,
            "department": html.unescape(match.group("department")).strip(),
            "posted_at": match.group("posted"),
            "workplace_type": "remote",
        }
