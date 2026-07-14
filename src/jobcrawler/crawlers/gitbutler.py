
import html
import re

SITE = "https://jobs.gitbutler.com/"
EXTRA_HOSTS = []


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip() or None


def metadata_from(fragment):
    result = {}
    rows = re.findall(
        r'<div[^>]*class="[^"]*job-meta-item[^"]*"[^>]*>(.*?)</div>',
        fragment,
        re.I | re.S,
    )
    for row in rows:
        parts = re.findall(r"<span[^>]*>(.*?)</span>", row, re.I | re.S)
        if len(parts) >= 2:
            label = clean_text(parts[0])
            value = clean_text(" ".join(parts[1:]))
            if label and value:
                result[label.rstrip(":").lower()] = value
    return result


def description_from(fragment):
    fragment = re.sub(r"<li[^>]*>", "\n- ", fragment, flags=re.I)
    fragment = re.sub(
        r"</?(?:p|h1|h2|h3|h4|ul|ol|li|br)[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)

    lines = []
    for line in fragment.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def crawl(ctx):
    board_html = ctx.fetch(SITE)

    listings = []
    cards = re.findall(
        r'<div[^>]*class="[^"]*job-card[^"]*"[^>]*>(.*?)(?=<div[^>]*class="[^"]*job-card|</main>)',
        board_html,
        re.I | re.S,
    )
    for card in cards:
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", card, re.I | re.S)
        url_match = re.search(
            r'<a[^>]*href="(/jobs/[^"?#]+)"[^>]*>', card, re.I | re.S
        )
        if not title_match or not url_match:
            continue

        path = html.unescape(url_match.group(1)).rstrip("/")
        if path.endswith("/apply"):
            continue

        listings.append(
            {
                "url": SITE.rstrip("/") + path,
                "title": clean_text(title_match.group(1)),
                "metadata": metadata_from(card),
            }
        )

    seen = set()
    for listing in listings:
        url = listing["url"]
        if url in seen:
            continue
        seen.add(url)

        detail_html = ctx.fetch(url)
        title_match = re.search(
            r'<h1[^>]*class="[^"]*job-title[^"]*"[^>]*>(.*?)</h1>',
            detail_html,
            re.I | re.S,
        )
        body_match = re.search(
            r'<div[^>]*class="[^"]*prose[^"]*"[^>]*>(.*?)</div>\s*</div>\s*<div[^>]*class="[^"]*job-apply',
            detail_html,
            re.I | re.S,
        )
        if not body_match:
            body_match = re.search(
                r'<div[^>]*class="[^"]*prose[^"]*"[^>]*>(.*?)</div>',
                detail_html,
                re.I | re.S,
            )

        title = clean_text(title_match.group(1)) if title_match else listing["title"]
        description = description_from(body_match.group(1)) if body_match else None
        metadata = dict(listing["metadata"])
        metadata.update(metadata_from(detail_html))
        location = metadata.get("location")

        if not title or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "GitButler",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if metadata.get("department"):
            fields["department"] = metadata["department"]
        if metadata.get("type"):
            fields["employment_type"] = metadata["type"]

        apply_match = re.search(
            r'<a[^>]*class="[^"]*btn-primary[^"]*"[^>]*href="([^"#?]+/apply)"',
            detail_html,
            re.I | re.S,
        )
        if not apply_match:
            apply_match = re.search(
                r'<a[^>]*href="([^"#?]+/apply)"[^>]*class="[^"]*btn-primary[^"]*"',
                detail_html,
                re.I | re.S,
            )
        if apply_match:
            apply_path = html.unescape(apply_match.group(1))
            if apply_path.startswith("/"):
                fields["apply_url"] = SITE.rstrip("/") + apply_path
            elif apply_path.startswith(SITE):
                fields["apply_url"] = apply_path

        yield fields
