
import json
import re
from html import unescape

SITE = "https://pydantic.dev/jobs/llms.txt"
EXTRA_HOSTS = []


def _full_text(rendered_html):
    # The source is the rendered article fragment from the trusted Astro props.
    text = re.sub(
        r"<(?:br|hr)\b[^>]*>|</(?:address|article|aside|blockquote|dd|div|dl|dt|"
        r"figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|"
        r"table|tbody|td|tfoot|th|thead|tr|ul)>",
        "\n",
        rendered_html,
        flags=re.I,
    )
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

_ENTRY_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]"
    r"\((?P<url>https://pydantic\.dev/jobs/[^)\s]+)\)"
    r" \u2014 posted (?P<date>\d{4}-\d{2}-\d{2})"
    r"(?: \(\[markdown\]\([^)]+\)\))?[ \t]*\n"
    r"[ \t]+(?P<department>[^\n]+)",
    re.MULTILINE,
)


def _encoded_value(value):
    # Astro's serializer wraps ordinary values as [type_tag, value].
    if isinstance(value, list) and len(value) > 1:
        return value[1]
    return None


def _job_props(page):
    for match in re.finditer(r"<astro-island\b[^>]*>", page, re.I | re.S):
        tag = match.group(0)
        if "JobPost" not in tag:
            continue
        attr = re.search(r'\bprops="([^"]*)"', tag, re.S)
        if attr:
            return json.loads(unescape(attr.group(1)))
    raise ValueError("JobPost Astro island not found")



def _label(markdown, name):
    pattern = (
        r"^[ \t]*[-*][ \t]+(?:\*\*)?" + re.escape(name) +
        r"[ \t]*:[ \t]*(?:\*\*)?[ \t]*(?P<value>.+?)[ \t]*$"
    )
    match = re.search(pattern, markdown, re.I | re.M)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group("value")).strip()
    return value or None


def crawl(ctx):
    index = ctx.fetch(SITE)
    seen = set()

    for match in _ENTRY_RE.finditer(index):
        entry = match.groupdict()
        url = entry["url"]
        if url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        props = _job_props(page)
        post = _encoded_value(props.get("post"))
        rendered = _encoded_value(props.get("content"))
        if not isinstance(post, dict) or not isinstance(rendered, str):
            continue

        markdown = _encoded_value(post.get("content"))
        if not isinstance(markdown, str):
            markdown = ""
        description = _full_text(rendered)
        if not description:
            continue

        explicit_location = _label(markdown, "Location")
        if explicit_location:
            location = explicit_location
        elif re.search(r"\b(?:fully|truly) remote\b", markdown, re.I):
            location = "Fully remote"
        else:
            continue

        data = {
            "url": url,
            "source": SITE,
            "title": entry["title"].strip(),
            "company": "Pydantic",
            "location": location,
            "description": description,
            "department": entry["department"].strip(),
            "posted_at": entry["date"],
        }

        slug = _encoded_value(post.get("slug"))
        if slug:
            data["external_id"] = slug

        employment_type = _label(markdown, "Type")
        if employment_type:
            data["employment_type"] = employment_type

        if re.search(r"\b(?:fully|truly) remote\b", markdown, re.I):
            data["workplace_type"] = "remote"

        compensation = _label(markdown, "Compensation")
        if compensation:
            data["salary"] = compensation

        yield data
