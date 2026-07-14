
import re
from html import unescape
from urllib.parse import urljoin

SITE = "https://directus.com/careers"
EXTRA_HOSTS = []

SITEMAP = "https://directus.com/sitemap-pages.xml"


def _clean_text(fragment):
    fragment = re.sub(r"(?is)<(script|style|noscript|svg)\b[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(?:p|li|h[1-6]|div|section|article)\s*>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    lines = []
    for line in unescape(fragment).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _tokens(html):
    text = _clean_text(html)
    return [line for line in text.splitlines() if line]


def _metadata(tokens, label, labels):
    try:
        index = tokens.index(label)
    except ValueError:
        return None
    if index + 1 >= len(tokens) or tokens[index + 1] in labels:
        return None
    return tokens[index + 1]


def _apply_url(html, base_url):
    for match in re.finditer(r'<a\b([^>]*)>(.*?)</a>', html, re.I | re.S):
        attrs, body = match.groups()
        if "apply" not in _clean_text(body).lower():
            continue
        href = re.search(r'\bhref=["\']([^"\']+)', attrs, re.I)
        if href:
            return urljoin(base_url, unescape(href.group(1)))
    return None


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP)
    discovered = re.findall(r"<loc>\s*([^<]+)\s*</loc>", sitemap, re.I)
    urls = []
    for raw_url in discovered:
        url = unescape(raw_url.strip())
        if re.match(r"^https://directus\.com/careers/[^/?#]+/?$", url, re.I):
            urls.append(url)

    seen = set()
    for discovered_url in urls:
        html = ctx.fetch(discovered_url)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if not title_match:
            continue
        title = _clean_text(title_match.group(1))
        if not title:
            continue

        canonical_match = re.search(
            r'<link\b[^>]*\brel=["\']canonical["\'][^>]*\bhref=["\']([^"\']+)',
            html,
            re.I,
        )
        if not canonical_match:
            canonical_match = re.search(
                r'<link\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*\brel=["\']canonical["\']',
                html,
                re.I,
            )
        url = urljoin(discovered_url, unescape(canonical_match.group(1))) if canonical_match else discovered_url
        if url in seen or not url.startswith("https://directus.com/careers/"):
            continue
        seen.add(url)

        tokens = _tokens(html)
        start = None
        for index, token in enumerate(tokens):
            if token == "Back" and title in tokens[index + 1:index + 8]:
                start = index
                break
        if start is None:
            continue

        title_index = None
        for index in range(start + 1, min(start + 8, len(tokens))):
            if tokens[index] == title:
                title_index = index
                break
        if title_index is None:
            continue

        end = None
        for index in range(title_index + 1, len(tokens)):
            if tokens[index] == "Apply Now":
                end = index
                break
        if end is None:
            continue

        role_tokens = tokens[title_index + 1:end]
        labels = {"Department", "Employment Type", "Experience Level", "Location"}
        department = _metadata(role_tokens, "Department", labels)
        employment_type = _metadata(role_tokens, "Employment Type", labels)
        location = _metadata(role_tokens, "Location", labels)

        body_end = len(role_tokens)
        for index, token in enumerate(role_tokens):
            if token in labels:
                body_end = index
                break
        description = "\n".join(role_tokens[:body_end]).strip()
        if not description or not location:
            continue

        yield {
            "url": url,
            "title": title,
            "company": "Directus",
            "location": location,
            "description": description,
            "source": SITE,
            "department": department,
            "employment_type": employment_type,
            "apply_url": _apply_url(html, url),
        }
