
import html
import re

from urllib.parse import urljoin, urlparse



SITE = "https://boundlesshq.com/careers/"
EXTRA_HOSTS = []


def _clean(fragment):
    fragment = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(
        r"<br\s*/?>|</(?:p|li|h[1-6]|div|section|ul|ol)>",
        "\n",
        fragment,
        flags=re.I,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    return re.sub(r"\n\s*\n+", "\n", fragment).strip()


def _attributes(tag):
    return {
        key.lower(): html.unescape(value)
        for key, quote, value in re.findall(
            r"""([:\w-]+)\s*=\s*(["'])(.*?)\2""", tag, re.S
        )
    }


def _balanced_elements(source, tag_name, predicate):
    token_re = re.compile(
        r"<(/?)" + re.escape(tag_name) + r"\b([^>]*)>",
        re.I | re.S,
    )
    stack = []
    results = []
    for match in token_re.finditer(source):
        closing = bool(match.group(1))
        if not closing:
            opening = match.group(0)
            stack.append((match.start(), predicate(_attributes(opening))))
            continue
        if not stack:
            continue
        start, matched = stack.pop()
        if matched:
            results.append(source[start:match.end()])
    return results


def _positions_section(source):
    match = re.search(
        r'<section\b[^>]*\bid=["\']positions["\'][^>]*>(.*?)</section>',
        source,
        re.I | re.S,
    )
    return match.group(1) if match else ""


def _listing_rows(source):
    section = _positions_section(source)
    return _balanced_elements(
        section,
        "div",
        lambda attrs: "bg-white-shade-1" in attrs.get("class", "").split(),
    )


def _paragraphs(fragment):
    return re.findall(r"<p\b[^>]*>(.*?)</p>", fragment, re.I | re.S)


def _role_from_row(row):
    paragraphs = _paragraphs(row)
    if len(paragraphs) < 4:
        return None

    title = _clean(paragraphs[0])
    values = {}
    for paragraph in paragraphs[1:]:
        label_match = re.search(
            r"<span\b[^>]*>\s*(Team|Location|Type)\s*:\s*</span>",
            paragraph,
            re.I | re.S,
        )
        if not label_match:
            continue
        without_label = re.sub(
            r"<span\b[^>]*>.*?</span>",
            " ",
            paragraph,
            count=1,
            flags=re.I | re.S,
        )
        value = _clean(without_label)
        if value:
            values[label_match.group(1).lower()] = value

    detail_url = None
    for anchor in re.findall(r"<a\b[^>]*>", row, re.I | re.S):
        attrs = _attributes(anchor)
        classes = attrs.get("class", "").split()
        if "btn--primary" in classes and attrs.get("href"):
            detail_url = urljoin(SITE, attrs["href"])
            break

    if not title or not detail_url or not values.get("location"):
        return None
    if urlparse(detail_url).hostname != urlparse(SITE).hostname:
        return None

    return {
        "title": title,
        "url": detail_url,
        "department": values.get("team"),
        "location": values["location"],
        "employment_type": values.get("type"),
    }


def _description(source):
    containers = _balanced_elements(
        source,
        "div",
        lambda attrs: "user-content" in attrs.get("class", "").split(),
    )
    if not containers:
        return None
    text = _clean(containers[0])
    return text or None


def _slug(url):
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def crawl(ctx):
    board_html = ctx.fetch(SITE)
    for row in _listing_rows(board_html):
        role = _role_from_row(row)
        if not role:
            continue

        detail_html = ctx.fetch(role["url"])
        description = _description(detail_html)
        if not description:
            continue

        fields = {
            "url": role["url"],
            "title": role["title"],
            "company": "Boundless",
            "location": role["location"],
            "description": description,
            "external_id": _slug(role["url"]),
            "source": SITE,
        }
        if role.get("department"):
            fields["department"] = role["department"]
        if role.get("employment_type"):
            fields["employment_type"] = role["employment_type"]

        yield fields
