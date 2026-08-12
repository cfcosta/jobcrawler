
import json
import re
from html import unescape
from jobcrawler.runtime.contract import Job

SITE = "https://pydantic.dev/jobs/llms.txt"
EXTRA_HOSTS = []

_ENTRY = re.compile(
    r"^- \[([^\]]+)\]\((https://pydantic\.dev/jobs/[^)\s]+)\)"
    r"\s+—\s+posted\s+(\d{4}-\d{2}-\d{2})"
    r"(?:\s+\(\[markdown\]\([^)]+\)\))?\s*$"
    r"\n[ \t]+([^\n]+)",
    re.MULTILINE,
)

_PROPS = re.compile(
    r'<astro-island\b[^>]*\bcomponent-export="JobDescriptionBody"[^>]*\bprops="(.*?)"',
    re.DOTALL,
)


def _description(page):
    match = _PROPS.search(page)
    if not match:
        return None

    props = json.loads(unescape(match.group(1)))
    content = props.get("postContent")
    if isinstance(content, list) and len(content) > 1:
        content = content[1]
    if not isinstance(content, str):
        return None

    content = unescape(content)
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(
        r"</?(?:section|h[1-6]|p|ul|ol|blockquote)[^>]*>",
        "\n",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(r"<li\b[^>]*>", "\n- ", content, flags=re.IGNORECASE)
    content = re.sub(r"</li\s*>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    content = unescape(content)
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r" *\n *", "\n", content)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() or None


def crawl(ctx):
    index = ctx.fetch(SITE)
    for match in _ENTRY.finditer(index):
        title, url, posted_at, department = match.groups()
        description = _description(ctx.fetch(url))
        if not description:
            continue
        yield Job(
            url=url,
            title=title,
            company="Pydantic",
            location="Remote (PT (UTC-8)–CET (UTC+1))",
            description=description,
            department=department.strip(),
            posted_at=posted_at,
            workplace_type="remote",
            source=SITE,
        )
