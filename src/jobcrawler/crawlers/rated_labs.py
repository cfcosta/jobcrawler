import re
from html import unescape
from urllib.parse import urldefrag, urljoin, urlparse

SITE = "https://ratedlabs.notion.site/Open-Roles-at-Rated-7054760f52bd4fd987adedce6c2b66e8"
EXTRA_HOSTS = []

_HOST = "ratedlabs.notion.site"
_ID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32})(?![0-9a-f])", re.I)
_NOISE = {
    "back", "back to top", "close", "copy link", "open in notion",
    "search", "share", "sign in",
}
_FIELD_LABELS = {
    "location": ("location",),
    "employment_type": (
        "employment type", "employment", "job type", "contract type",
    ),
    "workplace_type": (
        "workplace type", "workplace", "working model", "work model",
    ),
    "department": ("department", "team"),
    "posted_at": ("date posted", "posted at", "posted"),
    "expires_at": ("closing date", "expires at", "expires"),
}


def _clean(value):
    value = unescape(value or "")
    value = value.replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", value).strip()


def _strip_tags(value):
    value = re.sub(
        r"<(script|style|noscript|svg|template|canvas|iframe)\b[^>]*>.*?</\1\s*>",
        " ",
        value or "",
        flags=re.I | re.S,
    )
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return _clean(value)


def _lines(document):
    text = re.sub(
        r"<(script|style|noscript|svg|template|canvas|iframe|nav|footer)\b[^>]*>.*?</\1\s*>",
        " ",
        document or "",
        flags=re.I | re.S,
    )
    text = re.sub(
        r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|figcaption|figure|h[1-6]|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")

    result = []
    for raw_line in text.splitlines():
        line = _clean(raw_line)
        if line and (not result or line != result[-1]):
            result.append(line)
    return result


def _links(document):
    pattern = re.compile(
        r"<a\b[^>]*\bhref\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a\s*>",
        re.I | re.S,
    )
    result = []
    for match in pattern.finditer(document or ""):
        href = unescape(match.group(2)).strip()
        label = _strip_tags(match.group(3))
        result.append((href, label))
    return result


def _meta(document, key):
    for tag in re.findall(r"<meta\b[^>]*>", document or "", flags=re.I):
        name_match = re.search(
            r"\b(?:name|property)\s*=\s*([\"'])(.*?)\1", tag, flags=re.I | re.S
        )
        if not name_match or name_match.group(2).casefold() != key.casefold():
            continue
        content_match = re.search(
            r"\bcontent\s*=\s*([\"'])(.*?)\1", tag, flags=re.I | re.S
        )
        if content_match:
            return _clean(content_match.group(2))
    return None


def _canonical(url, base=SITE):
    absolute = urljoin(base, url)
    absolute, fragment = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.hostname != _HOST or parsed.scheme not in {"http", "https"}:
        return None
    return parsed._replace(
        scheme="https", query="", fragment=""
    ).geturl().rstrip("/")


def _page_id(url):
    match = _ID_RE.search((url or "").replace("-", ""))
    return match.group(1).lower() if match else None


def _role_links(document):
    root_id = _page_id(SITE)
    seen = set()

    for href, label in _links(document):
        url = _canonical(href)
        page_id = _page_id(url)
        if not url or not page_id or page_id == root_id or page_id in seen:
            continue
        if not label or label.casefold() in _NOISE:
            continue
        seen.add(page_id)
        yield url, label


def _metadata(lines):
    values = {}
    consumed = set()

    for index, line in enumerate(lines[:80]):
        normalized = line.casefold().strip()
        for field, labels in _FIELD_LABELS.items():
            found = False
            for label in labels:
                if normalized == label or normalized == label + ":":
                    if index + 1 < len(lines):
                        candidate = _clean(lines[index + 1])
                        if candidate and candidate.casefold() not in _NOISE:
                            values.setdefault(field, candidate)
                            consumed.update((index, index + 1))
                    found = True
                    break

                match = re.match(
                    r"^" + re.escape(label) + r"\s*(?::|[-–—])\s*(.+)$",
                    line,
                    re.I,
                )
                if match:
                    candidate = _clean(match.group(1))
                    if candidate:
                        values.setdefault(field, candidate)
                        consumed.add(index)
                    found = True
                    break
            if found:
                break

    return values, consumed


def _title(document, fallback):
    match = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", document or "", re.I | re.S)
    if match:
        value = _strip_tags(match.group(1))
        if value and value.casefold() not in _NOISE:
            return value

    for key in ("og:title", "twitter:title"):
        value = _meta(document, key)
        if value:
            value = re.sub(r"\s+[|–—-]\s+Notion\s*$", "", value, flags=re.I)
            if value:
                return value
    return _clean(fallback)


def _description(lines, title, consumed):
    result = []
    seen_long = set()

    for index, line in enumerate(lines):
        folded = line.casefold()
        if index in consumed or folded == title.casefold() or folded in _NOISE:
            continue
        if folded.startswith((
            "cookie preferences", "this site uses cookies", "accept all cookies",
        )):
            continue
        if len(line) > 100 and folded in seen_long:
            continue
        if len(line) > 100:
            seen_long.add(folded)
        result.append(line)

    return "\n\n".join(result).strip()


def _apply_url(document, page_url):
    labels = {
        "apply", "apply now", "apply for this role", "apply for this position",
        "submit application",
    }
    for href, label in _links(document):
        normalized = label.casefold().strip()
        if normalized in labels or normalized.startswith("apply "):
            candidate = urljoin(page_url, href)
            parsed = urlparse(candidate)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                return candidate
    return None


def crawl(ctx):
    try:
        board_html = ctx.fetch(SITE)
    except Exception:
        return

    for url, anchor_title in _role_links(board_html):
        try:
            document = ctx.fetch(url)
        except Exception:
            continue

        lines = _lines(document)
        title = _title(document, anchor_title)
        metadata, consumed = _metadata(lines)
        description = _description(lines, title, consumed)
        location = metadata.get("location")

        if not title or not location or not description:
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Rated Labs",
            "location": location,
            "description": description,
            "source": SITE,
        }

        for field in (
            "employment_type", "workplace_type", "department",
            "posted_at", "expires_at",
        ):
            value = metadata.get(field)
            if value:
                job[field] = value

        external_id = _page_id(url)
        if external_id:
            job["external_id"] = external_id

        apply_url = _apply_url(document, url)
        if apply_url:
            job["apply_url"] = apply_url

        yield job
