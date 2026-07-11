
import re
import html
from urllib.parse import urljoin

SITE = "https://upstash.com/careers"
EXTRA_HOSTS = []


def _fetch_text(ctx, url):
    res = ctx.fetch(url)
    if hasattr(res, "text"):
        return res.text
    if isinstance(res, bytes):
        return res.decode("utf-8", "replace")
    return str(res)


def _clean_text(value):
    if not value:
        return None
    value = re.sub(r"<(script|style|svg)[\s\S]*?</\1>", " ", value, flags=re.I)
    value = re.sub(r"<!--\s*-->", "", value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</(h[1-6]|p|li)>", "\n", value, flags=re.I)
    value = re.sub(r"</(ul|ol|div|section|article)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\u2019", "'")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip() or None


def _attr(tag, name):
    m = re.search(r"\b" + re.escape(name) + r'\s*=\s*["\']([^"\']+)["\']', tag, re.I)
    return html.unescape(m.group(1)) if m else None


def _first(pattern, text):
    m = re.search(pattern, text, re.I | re.S)
    return _clean_text(m.group(1)) if m else None


def _card_links(home):
    seen = set()
    for m in re.finditer(r'(<a\b[^>]*\bhref=["\'](/careers/[^"\']+)["\'][^>]*>)([\s\S]*?)</a>', home, re.I):
        href = html.unescape(m.group(2))
        body = m.group(3)
        title = _first(r"<h2\b[^>]*>([\s\S]*?)</h2>", body)
        if not title or href in seen:
            continue
        seen.add(href)
        yield urljoin(SITE, href)


def _job_from_detail(url, page):
    title = _first(r"<h1\b[^>]*>([\s\S]*?)</h1>", page)
    employment_type = _first(r"Job type\s*(?:<!--\s*-->)?\s*:</span>\s*<span[^>]*>([\s\S]*?)</span>", page)
    location = _first(r"Location\s*(?:<!--\s*-->)?\s*:</span>\s*<span[^>]*>([\s\S]*?)</span>", page)

    body_match = re.search(r'<div\b[^>]*class=["\'][^"\']*\bpost\b[^"\']*\bleading-p\b[^"\']*["\'][^>]*>([\s\S]*?)</div>\s*</div>\s*</div>\s*</article>', page, re.I)
    description = _clean_text(body_match.group(1)) if body_match else None
    if not description:
        article = re.search(r"<article\b[^>]*>([\s\S]*?)</article>", page, re.I)
        description = _clean_text(article.group(1)) if article else None

    apply_url = None
    header = page[: page.find("<div", page.find("</header>"))] if "</header>" in page else page
    mail = re.search(r'<a\b[^>]*href=["\'](mailto:[^"\']+)["\']', header, re.I)
    if not mail:
        mail = re.search(r'<a\b[^>]*href=["\'](mailto:[^"\']+)["\']', page, re.I)
    if mail:
        apply_url = html.unescape(mail.group(1))

    if not location:
        location = "Work from anywhere" if description and "Work from anywhere" in description else "Remote"

    job = {
        "url": url,
        "title": title,
        "company": "Upstash",
        "location": location,
        "description": description,
        "source": SITE,
    }
    if employment_type:
        job["employment_type"] = employment_type
    if location:
        job["workplace_type"] = "Remote" if "anywhere" in location.lower() or "remote" in location.lower() else None
    if apply_url:
        job["apply_url"] = apply_url
    job["external_id"] = url.rstrip("/").rsplit("/", 1)[-1]
    return {k: v for k, v in job.items() if v is not None and v != ""}


def crawl(ctx):
    home = _fetch_text(ctx, SITE)
    for url in _card_links(home):
        page = _fetch_text(ctx, url)
        job = _job_from_detail(url, page)
        if job.get("title") and job.get("description"):
            yield job
