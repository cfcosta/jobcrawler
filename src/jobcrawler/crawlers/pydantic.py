
import html
import re

from jobcrawler.runtime.contract import Job

SITE = "https://pydantic.dev/jobs/llms.txt"
EXTRA_HOSTS = []


def text_from_html(fragment):
    fragment = re.sub(
        r"<(script|style|svg|button)\b[^>]*>.*?</\1\s*>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    fragment = re.sub(r"<li\b[^>]*>", "\n- ", fragment, flags=re.I)
    fragment = re.sub(
        r"</?(?:article|blockquote|div|dl|dt|dd|h[1-6]|hr|ol|p|pre|section|table|tr|ul)\b[^>]*>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<[^>]+>", "", fragment)
    value = html.unescape(fragment)
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_fragment(page, start_pattern, end_tag):
    start = re.search(start_pattern, page, re.I)
    if not start:
        return None
    end = page.find(end_tag, start.end())
    if end < 0:
        return None
    return page[start.start():end + len(end_tag)]


def parse_details(page):
    aside = extract_fragment(
        page,
        r'<aside\b[^>]*class="[^"]*\bjob-details\b[^"]*"[^>]*>',
        "</aside>",
    )
    if not aside:
        return {}

    details = {}
    pairs = re.findall(
        r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>",
        aside,
        re.I | re.S,
    )
    for label_html, value_html in pairs:
        label = text_from_html(label_html).strip().lower()
        value = text_from_html(value_html).strip()
        if label and value:
            details[label] = value
    return details


def parse_index(index):
    pattern = re.compile(
        r"^- \[(?P<title>[^\]]+)\]"
        r"\((?P<url>https://pydantic\.dev/jobs/[^)\s]+)\)"
        r"\s+.+?\s+posted\s+(?P<posted>\d{4}-\d{2}-\d{2})"
        r"[^\n]*\n[ \t]+(?P<department>[^\n]+)",
        re.M,
    )
    seen = set()
    for match in pattern.finditer(index):
        url = match.group("url")
        if url.endswith(".md") or url in seen:
            continue
        seen.add(url)
        yield {
            "title": match.group("title").strip(),
            "url": url,
            "posted_at": match.group("posted"),
            "department": match.group("department").strip(),
        }


def crawl(ctx):
    index = ctx.fetch(SITE)

    for listing in parse_index(index):
        page = ctx.fetch(listing["url"])
        details = parse_details(page)
        article = extract_fragment(
            page,
            r'<article\b[^>]*class="[^"]*\bjob-body\b[^"]*"[^>]*>',
            "</article>",
        )
        if not article:
            continue

        description = text_from_html(article)
        if not description:
            continue

        location = details.get("location")
        fields = {
            "url": listing["url"],
            "title": listing["title"],
            "company": "Pydantic",
            "location": location,
            "description": description,
            "source": SITE,
            "posted_at": listing["posted_at"],
            "department": details.get("department") or listing["department"],
            "employment_type": details.get("employment type"),
            "salary": details.get("compensation"),
        }
        if location and "fully remote" in location.lower():
            fields["workplace_type"] = "remote"

        yield Job(**{key: value for key, value in fields.items() if value is not None})
