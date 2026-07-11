"""Crawler for Hetzner's self-hosted careers board (career.hetzner.com)."""

import html
import re

SITE = "https://career.hetzner.com/en/jobs/"
EXTRA_HOSTS = []

COMPANY = "Hetzner"
BASE = "https://career.hetzner.com"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_ROW_RE = re.compile(r'<tr class="alternative_[01]">(.*?)</tr>', re.S)
_LINK_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_COL2_RE = re.compile(r'real_table_col2">(.*?)</td>', re.S)
_COL3_RE = re.compile(r'real_table_col3">(.*?)</td>', re.S)
_COL4_RE = re.compile(r'real_table_col4">(.*?)</td>', re.S)
_ID_RE = re.compile(r"-eng-([jf]\d+)/?$")

# Start of the job body (introduction/task/profile/benefits sections). The
# recruiter card (scheme-user) that follows embeds a huge base64 portrait, so
# the description is cut before it (with a data:image fallback guard).
_DESC_START = '<div class="scheme-content scheme-margin'
_DESC_END = '<div class="scheme-content scheme-user"'


def _strip_tags(raw: str) -> str:
    """Unescape HTML entities then strip tags, normalizing whitespace."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _description(detail_html: str) -> str:
    start = detail_html.find(_DESC_START)
    if start < 0:
        return ""
    end = detail_html.find(_DESC_END, start)
    b64 = detail_html.find("data:image", start)
    if b64 != -1 and (end == -1 or b64 < end):
        end = b64
    if end < 0:
        end = start + 20000
    return _strip_tags(detail_html[start:end])


def crawl(ctx):
    """Yield one Job dict per open role listed on Hetzner's careers board.

    The listing is a server-rendered table (title, location, category,
    employment type); each role's description is fetched from its detail page.
    """
    listing = ctx.fetch(SITE)
    seen = set()

    for row in _ROW_RE.findall(listing):
        link = _LINK_RE.search(row)
        if not link:
            continue
        href = html.unescape(link.group(1)).strip()
        title = _strip_tags(link.group(2))
        if not href or not title:
            continue
        if href in seen:
            continue
        seen.add(href)

        url = href if href.startswith("http") else BASE + href

        col2 = _COL2_RE.search(row)
        col3 = _COL3_RE.search(row)
        col4 = _COL4_RE.search(row)
        location = _strip_tags(col2.group(1)) if col2 else ""
        department = _strip_tags(col3.group(1)) if col3 else ""
        employment_type = _strip_tags(col4.group(1)) if col4 else ""

        detail_html = ctx.fetch(url)
        description = _description(detail_html)
        if not (url and title and description):
            continue

        out: dict[str, object] = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "apply_url": url.rstrip("/") + "/apply/",
        }

        mid = _ID_RE.search(href)
        if mid:
            out["external_id"] = mid.group(1)

        if department:
            out["department"] = department
        if employment_type:
            out["employment_type"] = employment_type

        locations = [p.strip() for p in location.split(",") if p.strip()]
        if len(locations) > 1:
            out["locations"] = locations

        yield out
