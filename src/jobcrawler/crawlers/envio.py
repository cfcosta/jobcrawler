
import re

SITE = "https://envio-dev.notion.site"
EXTRA_HOSTS = []

_ROLE_RE = re.compile(
    r"^https://envio-dev[.]notion[.]site/[^?#]*-([0-9a-f]{32})/?(?:[?#].*)?$",
    re.I,
)


def _entities(value):
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&#x27;": "'",
        "&nbsp;": " ",
        "&#160;": " ",
    }
    for encoded, decoded in replacements.items():
        value = value.replace(encoded, decoded)
    return re.sub(
        r"&#(?:x([0-9a-f]+)|([0-9]+));",
        lambda match: chr(
            int(match.group(1), 16)
            if match.group(1)
            else int(match.group(2))
        ),
        value,
        flags=re.I,
    )


def _text(fragment):
    fragment = re.sub(
        r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|"
        r"figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|"
        r"td|th|tr|ul)\b[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b.*?</style>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = _entities(fragment)

    lines = []
    for line in fragment.splitlines():
        line = " ".join(line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _meta_description(document):
    match = re.search(
        r'<meta\b(?=[^>]*\bname=["\']description["\'])'
        r'(?=[^>]*\bcontent=["\']([^"\']+)["\'])[^>]*>',
        document,
        flags=re.I,
    )
    if not match:
        match = re.search(
            r'<meta\b(?=[^>]*\bcontent=["\']([^"\']+)["\'])'
            r'(?=[^>]*\bname=["\']description["\'])[^>]*>',
            document,
            flags=re.I,
        )
    return _entities(match.group(1)).strip() if match else None


def _fields(meta):
    fields = {}
    if not meta:
        return fields

    values = [part.strip() for part in meta.split("|") if part.strip()]
    lowered = [value.lower() for value in values]

    location_values = [
        value
        for value in values
        if value.lower() in ("remote", "cape town", "london")
    ]
    if location_values:
        fields["location"] = " | ".join(location_values)
        fields["locations"] = location_values

    if "remote" in lowered:
        fields["workplace_type"] = "remote"

    for value in values:
        normalized = value.lower().replace(" ", "").replace("-", "")
        if normalized == "fulltime":
            fields["employment_type"] = "Full-time"
            break

    return fields


def crawl(ctx):
    index_response = ctx.fetch(SITE)
    index_html = index_response

    roles = []
    for match in re.finditer(
        r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        index_html,
        flags=re.I | re.S,
    ):
        url = match.group(1).split("#", 1)[0].split("?", 1)[0].rstrip("/")
        role_match = _ROLE_RE.match(url)
        title = _text(match.group(2)).strip()
        if role_match and title:
            roles.append((url, title, role_match.group(1).lower()))

    seen = set()
    for url, title, external_id in roles:
        if url in seen:
            continue
        seen.add(url)

        detail_response = ctx.fetch(url)
        document = detail_response
        body_match = re.search(
            r"<body\b[^>]*>(.*?)</body>",
            document,
            flags=re.I | re.S,
        )
        if not body_match:
            continue

        description = _text(body_match.group(1))
        if not description:
            continue

        fields = _fields(_meta_description(document))
        fields["external_id"] = external_id

        item = {
            "url": url,
            "title": title,
            "company": "Envio",
            "description": description,
            "source": SITE,
        }
        item.update(fields)
        yield item
