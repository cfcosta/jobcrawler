
import json
import re
from html import unescape

from jobcrawler.runtime.contract import Job


SITE = "https://pydantic.dev/jobs/llms.txt"
EXTRA_HOSTS = []

_INDEX_ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]"
    r"\((?P<url>https://pydantic\.dev/jobs/[^)\s]+)\)"
    r"\s+\u2014\s+posted\s+(?P<date>\d{4}-\d{2}-\d{2})"
    r"(?:\s+\(\[markdown\]\([^)]+\)\))?\s*$"
    r"\n[ \t]+(?P<department>[^\n]+)",
    re.MULTILINE,
)

_PROPS_RE = re.compile(
    r"<astro-island\b[^>]*\bprops=\"([^\"]+)\"",
    re.IGNORECASE | re.DOTALL,
)

_BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|figcaption|"
    r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
    r"tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
    re.IGNORECASE,
)

_BUTTON_RE = re.compile(
    r"<button\b[^>]*>.*?</button>",
    re.IGNORECASE | re.DOTALL,
)

_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)


def _find_body(value):
    if isinstance(value, dict):
        for key in ("postContent", "content"):
            if key in value:
                body = _find_body(value[key])
                if body:
                    return body
        for child in value.values():
            body = _find_body(child)
            if body:
                return body
    elif isinstance(value, list):
        for child in value:
            body = _find_body(child)
            if body:
                return body
    elif isinstance(value, str) and "<section" in value:
        return value
    return None


def _html_to_text(body_html):
    text = unescape(body_html)
    text = _BUTTON_RE.sub("", text)
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)

    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _description_from_page(page):
    for encoded_props in _PROPS_RE.findall(page):
        try:
            props = json.loads(unescape(encoded_props))
        except (TypeError, ValueError):
            continue

        body_html = _find_body(props)
        if body_html:
            description = _html_to_text(body_html)
            if description:
                return description

    raise ValueError("Full job description Astro island was not found")


def _field(description, label):
    match = re.search(
        r"^(?:-\s*)?" + re.escape(label) + r":\s*(.+?)\s*$",
        description,
        re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def _location(description):
    labeled = _field(description, "Location")
    if labeled:
        return labeled

    timezone = re.search(
        r"(Live and work in a timezone between PT \(UTC-8\) and CET \(UTC\+1\))",
        description,
        re.IGNORECASE,
    )
    if timezone:
        return timezone.group(1)

    remote = re.search(
        r"(Work from anywhere within our timezone range(?:\s*-\s*no office requirements)?)",
        description,
        re.IGNORECASE,
    )
    if remote:
        return remote.group(1)

    raise ValueError("Posting does not state a location")


def crawl(ctx):
    index = ctx.fetch(SITE)

    for match in _INDEX_ENTRY_RE.finditer(index):
        entry = match.groupdict()
        description = _description_from_page(ctx.fetch(entry["url"]))

        fields = {
            "url": entry["url"],
            "title": entry["title"].strip(),
            "company": "Pydantic",
            "location": _location(description),
            "description": description,
            "source": SITE,
            "department": entry["department"].strip(),
            "posted_at": entry["date"],
        }

        if re.search(r"\b(?:fully|truly) remote\b", description, re.IGNORECASE):
            fields["workplace_type"] = "remote"

        employment_type = _field(description, "Type")
        if employment_type:
            fields["employment_type"] = employment_type

        compensation = _field(description, "Compensation")
        if compensation:
            fields["salary"] = compensation

        yield Job(**fields)
