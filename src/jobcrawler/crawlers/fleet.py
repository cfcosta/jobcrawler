import re
from html import unescape
from urllib.parse import urljoin, urlparse

SITE = "https://fleetdm.com/handbook/company"
EXTRA_HOSTS = []


def _text(response):
    if isinstance(response, str):
        return response
    return response.text


def _clean_title(title):
    title = unescape(title)
    return re.sub(r"^[^\w(]+", "", title).strip()


def _discover(html):
    match = re.search(
        r"openPositions\s*:\s*unescape\(\s*\[(.*?)\]\s*\)",
        html,
        re.DOTALL,
    )
    if not match:
        return []

    jobs = []
    seen = set()
    for item in re.findall(r"\{(.*?)\}", match.group(1), re.DOTALL):
        title_match = re.search(
            r"(?:jobTitle|title)\s*:\s*(['\"])(.*?)\1",
            item,
            re.DOTALL,
        )
        url_match = re.search(
            r"url\s*:\s*(['\"])(.*?)\1",
            item,
            re.DOTALL,
        )
        if not title_match or not url_match:
            continue

        url = urljoin(SITE, unescape(url_match.group(2)))
        if urlparse(url).hostname != "fleetdm.com" or url in seen:
            continue

        seen.add(url)
        jobs.append((_clean_title(title_match.group(2)), url))
    return jobs


def _plain_text(fragment):
    fragment = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        fragment,
        flags=re.IGNORECASE | re.DOTALL,
    )
    fragment = re.sub(
        r"<(?:br|p|li|h1|h2|h3|h4)\b[^>]*>",
        "\n",
        fragment,
        flags=re.IGNORECASE,
    )
    fragment = re.sub(
        r"</(?:p|li|h1|h2|h3|h4)>",
        "\n",
        fragment,
        flags=re.IGNORECASE,
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = unescape(fragment)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_posting(html, page_url):
    start = re.search(
        r'<div\b[^>]*\bid=["\']body-content["\'][^>]*>',
        html,
        re.IGNORECASE,
    )
    if not start:
        return None, None, None

    end = html.find("{{page.pageTitle}}", start.end())
    if end < 0:
        end = html.find('purpose="page-footer"', start.end())
    if end < 0:
        return None, None, None

    fragment = html[start.end():end]
    description = _plain_text(fragment)

    apply_url = None
    for href, label_html in re.findall(
        r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        fragment,
        re.IGNORECASE | re.DOTALL,
    ):
        label = _plain_text(label_html)
        candidate = urljoin(page_url, unescape(href))
        if (
            urlparse(candidate).hostname == "fleetdm.com"
            and ("apply" in href.lower() or "apply" in label.lower())
        ):
            apply_url = candidate
            break

    salary_match = re.search(
        r"The salary range for this role is "
        r"\$[0-9,]+\s*-\s*\$[0-9,]+\+?\."
        r"(?: Fleet provides[^.\n]*\.)?",
        description,
    )
    salary = salary_match.group(0) if salary_match else None
    return description, apply_url, salary


def crawl(ctx):
    board_html = _text(ctx.fetch(SITE))

    for title, url in _discover(board_html):
        page_html = _text(ctx.fetch(url))
        description, apply_url, salary = _extract_posting(page_html, url)
        if not title or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Fleet Device Management Inc.",
            "location": "Remote",
            "workplace_type": "remote",
            "description": description,
            "source": SITE,
        }
        if apply_url:
            fields["apply_url"] = apply_url
        if salary:
            fields["salary"] = salary

        yield fields
