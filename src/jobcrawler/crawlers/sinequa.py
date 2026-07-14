import re
from html import unescape
from urllib.parse import urljoin


SITE = "https://www.sinequa.com/careers/"
EXTRA_HOSTS = []

JOB_SITEMAP = "https://www.sinequa.com/wp-sitemap-posts-job-1.xml"


def clean_text(fragment):
    if not fragment:
        return None

    fragment = re.sub(
        r"<(?:script|style|noscript|template)\b[^>]*>.*?</(?:script|style|noscript|template)>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"<br\s*/?>|</(?:p|li|ul|ol|h[1-6]|div|section)>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = fragment.replace("-->", " ")
    fragment = unescape(fragment)

    lines = []
    for line in fragment.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)

    return "\n".join(lines).strip() or None


def cards(document):
    result = []
    pattern = re.compile(
        r'''<a\b[^>]*href=["']([^"']*teamtailor\.com/jobs/[^"']+)["'][^>]*>(.*?)</a>''',
        re.I | re.S,
    )

    for match in pattern.finditer(document):
        block = match.group(2)
        title_match = re.search(
            r"<h3\b[^>]*>(.*?)</h3>",
            block,
            re.I | re.S,
        )
        tags = [
            clean_text(value)
            for value in re.findall(
                r"<li\b[^>]*>(.*?)</li>",
                block,
                re.I | re.S,
            )
        ]
        tags = [value for value in tags if value]

        if not title_match:
            continue

        apply_url = unescape(match.group(1))
        id_match = re.search(r"/jobs/(\d+)(?:-|/|$)", apply_url)

        result.append({
            "title": clean_text(title_match.group(1)),
            "department": tags[0] if tags else None,
            "location": tags[1] if len(tags) > 1 else None,
            "apply_url": apply_url,
            "external_id": id_match.group(1) if id_match else None,
        })

    return result


def sitemap_urls(document):
    return [
        unescape(value.strip())
        for value in re.findall(
            r"<loc>\s*([^<]+?)\s*</loc>",
            document,
            re.I,
        )
    ]


def detail_title(document):
    match = re.search(
        r"<h1\b[^>]*>(.*?)</h1>",
        document,
        re.I | re.S,
    )
    return clean_text(match.group(1)) if match else None


def detail_description(document):
    heading = re.search(
        r"<h1\b[^>]*>.*?</h1>",
        document,
        re.I | re.S,
    )
    if not heading:
        return None

    footer = re.search(r"<footer\b", document[heading.end():], re.I)
    end = heading.end() + footer.start() if footer else len(document)
    description = clean_text(document[heading.end():end])

    # Some WordPress job records are title-only shells.
    if not description or len(description) < 500:
        return None

    return description


def crawl(ctx):
    first = ctx.fetch(SITE)
    board_cards = cards(first)

    page_numbers = [
        int(value)
        for value in re.findall(r"[?&]sf_paged=(\d+)", first)
    ]
    last_page = max(page_numbers, default=1)

    for page_number in range(2, last_page + 1):
        page_url = urljoin(SITE, "?sf_paged=%d" % page_number)
        board_cards.extend(cards(ctx.fetch(page_url)))

    urls = sitemap_urls(ctx.fetch(JOB_SITEMAP))

    for index, url in enumerate(urls):
        if index >= len(board_cards):
            break

        card = board_cards[index]
        document = ctx.fetch(url)
        description = detail_description(document)
        title = detail_title(document) or card.get("title")
        location = card.get("location")

        if not title or not location or not description:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Sinequa",
            "location": location,
            "description": description,
            "source": JOB_SITEMAP,
        }

        for field in ("department", "external_id", "apply_url"):
            value = card.get(field)
            if value:
                item[field] = value

        yield item