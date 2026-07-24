import html
import json
import re
from urllib.parse import urljoin

from jobcrawler.runtime.contract import Job

SITE = "https://zuplo.com/careers.md"
EXTRA_HOSTS = []


def _text(response):
    text = response if isinstance(response, str) else response.text
    match = re.search(r"<pre\b[^>]*>(.*?)</pre>", text, re.I | re.S)
    if match:
        text = match.group(1)
    return html.unescape(text)


def _frontmatter(document):
    match = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?", document, re.S)
    if not match:
        return {}, document

    values = {}
    for line in match.group(1).splitlines():
        field = re.match(r"^([A-Za-z][A-Za-z0-9]*):\s*(.*?)\s*$", line)
        if not field:
            continue
        key, value = field.groups()
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        values[key] = value
    return values, document[match.end():].strip()


def crawl(ctx):
    board = _text(ctx.fetch("https://zuplo.com/careers.md"))
    section = re.search(
        r"^## Open positions\s*$([\s\S]*?)(?=^##\s|\Z)",
        board,
        re.I | re.M,
    )
    if not section:
        return

    rows = re.findall(
        r"^\|\s*\[([^\]]+)\]\((/careers/[^)\s]+)\)\s*"
        r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$",
        section.group(1),
        re.M,
    )
    for listed_title, path, listed_location, listed_type in rows:
        detail_url = urljoin("https://zuplo.com", path.rstrip("/") + ".md")
        fields, description = _frontmatter(_text(ctx.fetch(detail_url)))
        if fields.get("pageType") != "career":
            continue

        # Remove non-content Astro/MDX components while retaining the full JD.
        description = re.sub(
            r"(?m)^\s*<[A-Za-z][^>\n]*/>\s*$",
            "",
            description,
        )
        description = re.sub(r"\n{3,}", "\n\n", description).strip()

        canonical = fields.get("canonicalUrl") or urljoin("https://zuplo.com", path)
        title = fields.get("title") or listed_title.strip()
        location = fields.get("location") or listed_location.strip()
        employment_type = fields.get("employmentType") or listed_type.strip()

        yield Job(
            source=SITE,
            url=canonical,
            title=title,
            company="Zuplo",
            location=location,
            description=description,
            employment_type=employment_type,
            apply_url=canonical,
        )
