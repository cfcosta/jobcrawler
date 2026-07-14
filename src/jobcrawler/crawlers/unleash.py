
import re
from html import unescape
from urllib.parse import urljoin, urlparse


SITE = "https://www.getunleash.io/careers"
EXTRA_HOSTS = []


def _clean_html(value):
    value = re.sub(r"<(?:script|style|svg|noscript)\b[^>]*>.*?</(?:script|style|svg|noscript)>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", " | ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _main_content(page):
    match = re.search(r"<main\b[^>]*>(.*?)</main>", page, re.I | re.S)
    if not match:
        return "", ""

    main_html = match.group(1)
    body = re.sub(
        r"<(?:script|style|svg|noscript)\b[^>]*>.*?</(?:script|style|svg|noscript)>",
        "",
        main_html,
        flags=re.I | re.S,
    )

    lines = []
    for block in re.finditer(
        r"<(h1|h2|h3|h4|p|li)\b[^>]*>(.*?)</\1>",
        body,
        re.I | re.S,
    ):
        tag = block.group(1).lower()
        text = _clean_html(block.group(2))
        if text:
            lines.append(("- " if tag == "li" else "") + text)

    return "\n".join(lines).strip(), main_html


def _apply_url(main_html):
    candidates = []
    for match in re.finditer(
        r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        main_html,
        re.I | re.S,
    ):
        href = unescape(match.group(1)).strip()
        label = _clean_html(match.group(2)).lower()
        host = (urlparse(href).hostname or "").lower()
        if (
            "apply" in label
            or host in {"cord.com", "www.cord.com"}
            or host.endswith(".teamtailor.com")
        ):
            candidates.append(href)
    return candidates[-1] if candidates else None


def _field(description, name):
    match = re.search(
        r"(?:^|\n|\|\s*)" + re.escape(name) + r":\s*([^|\n]+)",
        description,
        re.I,
    )
    return match.group(1).strip() if match else None


def crawl(ctx):
    homepage = ctx.fetch(SITE)
    cards = re.findall(
        r'<li\b[^>]*class=["\'][^"\']*Card-module-[^"\']*__container[^"\']*["\'][^>]*>'
        r"(.*?)</li>",
        homepage,
        re.I | re.S,
    )

    seen = set()
    for card in cards:
        title_match = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.I | re.S)
        href_match = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\']', card, re.I)
        if not title_match or not href_match:
            continue

        title = _clean_html(title_match.group(1))
        href = unescape(href_match.group(1)).strip()
        if title.lower() == "open application" or href.rstrip("/") == "/open-application":
            continue

        url = urljoin(SITE, href)
        if url in seen:
            continue
        seen.add(url)

        page = ctx.fetch(url)
        description, main_html = _main_content(page)
        if not title or not description:
            continue

        data = {
            "url": url,
            "title": title,
            "company": "Unleash",
            "description": description,
            "source": SITE,
        }

        employment_type = _field(description, "Employment type")
        if employment_type:
            data["employment_type"] = employment_type

        remote_working = _field(description, "Remote working")
        explicit_location = _field(description, "Location")
        if explicit_location:
            data["location"] = explicit_location
            if "hybrid" in explicit_location.lower():
                data["workplace_type"] = "Hybrid"
        elif remote_working:
            data["location"] = remote_working
            data["workplace_type"] = remote_working
        elif "remote-first company" in description.lower():
            data["location"] = "Remote"

        department = _field(description, "Department")
        if department:
            data["department"] = department

        salary = _field(description, "Base salary")
        if salary:
            data["salary"] = salary

        apply_url = _apply_url(main_html)
        if apply_url:
            data["apply_url"] = apply_url

        yield data
