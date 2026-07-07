import html
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

SITE = "https://quickwit.io/jobs/distributed-software-engineer"
EXTRA_HOSTS = []

MAX_POSTINGS = 30

_ZW = "​"
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_LOC_IN_RE = re.compile(
    r"(?i)open (?:for|to)[^.\n]*?\bin\s+([A-Za-z][A-Za-z ,/&-]+?)\s*[.\n]"
)
_BASED_RE = re.compile(r"(?i)\bbased in\s+([A-Za-z][A-Za-z ,/&-]+?)\s*[.\n]")
_REMOTE_RE = re.compile(r"(?i)fully distributed|fully remote|\bremote\b")
_EMP_RE = re.compile(
    r"(?i)\bthis is an?\s+(full[- ]time|part[- ]time|contract|internship)\b"
)
_COMP_RE = re.compile(
    r"(?i)compensation range[^\n]*?\bis\s+(.+?)(?:\s+and follows\b|\.\s|\n|$)"
)

_EMPLOYMENT = {
    "full-time": "Full-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "internship": "Internship",
}


def _clean(fragment):
    if not fragment:
        return None
    text = html.unescape(fragment)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(
        r"(?i)</\s*(p|div|li|h[1-6]|section|article|tr|ul|ol|table)\s*>", "\n", text
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace(_ZW, "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _title(article, page):
    m = _H1_RE.search(article)
    if m:
        value = _clean(m.group(1))
        if value:
            return value
    m = _TITLE_RE.search(page)
    if m:
        value = _clean(m.group(1))
        if value:
            return re.sub(r"\s*\|\s*Quickwit\s*$", "", value).strip() or None
    return None


def _location(description):
    for rx in (_LOC_IN_RE, _BASED_RE):
        m = rx.search(description)
        if m:
            loc = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
            if loc:
                return loc
    if _REMOTE_RE.search(description):
        return "Remote"
    return None


def _employment(description):
    m = _EMP_RE.search(description)
    if not m:
        return None
    return _EMPLOYMENT.get(m.group(1).lower().replace(" ", "-"))


def _salary(description):
    m = _COMP_RE.search(description)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip(" .") or None


def _apply_url(description):
    m = _EMAIL_RE.search(description)
    return "mailto:" + m.group(0) if m else None


def _job_urls(xml, host):
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []
    urls = []
    for url_el in root.findall(".//{*}url"):
        loc_el = url_el.find("{*}loc")
        if loc_el is None or not loc_el.text:
            continue
        loc = loc_el.text.strip()
        parts = urlsplit(loc)
        if parts.netloc != host:
            continue
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) == 2 and segments[0] == "jobs":
            urls.append(loc)
    return urls


def _clean_dict(data):
    return {k: v for k, v in data.items() if v not in (None, "")}


def crawl(ctx):
    split = urlsplit(SITE)
    host = split.netloc
    sitemap = split.scheme + "://" + host + "/sitemap.xml"

    try:
        urls = _job_urls(ctx.fetch(sitemap), host)
    except Exception:
        urls = []
    if SITE not in urls:
        urls = [SITE] + urls

    seen = set()
    for url in urls[:MAX_POSTINGS]:
        if url in seen:
            continue
        seen.add(url)
        try:
            page = ctx.fetch(url)
        except Exception:
            continue
        m = _ARTICLE_RE.search(page)
        if not m:
            continue
        article = m.group(1)
        title = _title(article, page)
        description = _clean(article)
        if not title or not description or "Page Not Found" in title:
            continue
        location = _location(description)
        if not location:
            continue

        out = {
            "url": url,
            "title": title,
            "company": "Quickwit",
            "location": location,
            "description": description,
            "source": "quickwit",
            "external_id": urlsplit(url).path.rsplit("/", 1)[-1] or None,
            "workplace_type": "Remote" if _REMOTE_RE.search(description) else None,
            "employment_type": _employment(description),
            "salary": _salary(description),
            "apply_url": _apply_url(description),
        }
        yield _clean_dict(out)
