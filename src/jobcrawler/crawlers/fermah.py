
import re

SITE = "https://fermah.xyz/careers"
EXTRA_HOSTS = []


def unescape(value):
    names = {
        "amp": "&", "lt": "<", "gt": ">", "quot": '"',
        "apos": "'", "nbsp": " ", "ndash": "-", "mdash": "-",
        "rsquo": "'", "lsquo": "'", "ldquo": '"', "rdquo": '"',
    }

    def replace(match):
        entity = match.group(1)
        if entity.startswith("#x") or entity.startswith("#X"):
            try:
                return chr(int(entity[2:], 16))
            except ValueError:
                return match.group(0)
        if entity.startswith("#"):
            try:
                return chr(int(entity[1:]))
            except ValueError:
                return match.group(0)
        return names.get(entity, match.group(0))

    return re.sub(r"&([A-Za-z]+|#[0-9]+|#[xX][0-9A-Fa-f]+);", replace, value)


def text_content(value):
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(
        r"</?(?:p|li|h1|h2|h3|h4|h5|h6|tr|table|ul|ol|hr|br)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def crawl(ctx):
    listing = ctx.fetch(SITE)
    cards = re.findall(
        r'<a href="(/careers/([0-9a-fA-F-]{36}))"[^>]*>(.*?)</a>',
        listing,
        re.S,
    )

    seen = set()
    for path, external_id, card in cards:
        url = "https://fermah.xyz" + path
        if url in seen:
            continue
        seen.add(url)

        title_match = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.S)
        if not title_match:
            continue
        title = text_content(title_match.group(1))

        spans = [
            text_content(value)
            for value in re.findall(r"<span\b[^>]*>(.*?)</span>", card, re.S)
        ]
        spans = [value for value in spans if value and value != "View Details"]

        detail_page = ctx.fetch(url)
        body_match = re.search(
            r'<div class="prose prose-lg max-w-none">(.*?)'
            r'</div><!--\]-->\s*<div class="mt-12',
            detail_page,
            re.S,
        )
        if not body_match:
            continue

        body_html = body_match.group(1)
        description = text_content(body_html)
        if not description:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Fermah",
            "location": "Not specified",
            "description": description,
            "source": SITE,
            "external_id": external_id,
        }
        if spans:
            item["department"] = spans[0]
        if len(spans) > 1:
            item["employment_type"] = spans[1]

        apply_match = re.search(
            r'<a\b[^>]*href="([^"]+)"[^>]*>\s*form\.?\s*</a>',
            body_html,
            re.I | re.S,
        )
        if apply_match:
            item["apply_url"] = unescape(apply_match.group(1))

        yield item
