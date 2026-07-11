
import re
import html
from urllib.parse import urljoin

SITE = "https://www.siderolabs.com/careers/"
EXTRA_HOSTS = []

COMPANY = "Sidero Labs"


def _first(pattern, text, flags=re.S | re.I):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def _clean_text(value):
    if not value:
        return None
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|dt|dd|section|article|ul|ol)\s*>", "\n", value)
    value = re.sub(r"(?i)<\s*li\b[^>]*>", "- ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _attrs(tag):
    return dict((m.group(1).lower(), html.unescape(m.group(2))) for m in re.finditer(r'([:\w-]+)\s*=\s*"([^"]*)"', tag))


def _vacancy_cards(page):
    for m in re.finditer(r"(?is)<a\b([^>]*\brd-vacancy-card\b[^>]*)>(.*?)</a>", page):
        attrs = _attrs(m.group(1))
        href = attrs.get("href")
        body = m.group(2)
        if not href:
            continue
        title = _clean_text(_first(r'<h3\b[^>]*class="[^"]*\brd-vacancy-card__title\b[^"]*"[^>]*>(.*?)</h3>', body))
        tags = [_clean_text(x) for x in re.findall(r'<span\b[^>]*class="[^"]*\brd-vacancy-card__tag\b[^"]*"[^>]*>(.*?)</span>', body, re.S | re.I)]
        tags = [x for x in tags if x]
        yield {
            "url": urljoin(SITE, href),
            "title": title,
            "employment_type": tags[0] if len(tags) > 0 else None,
            "location": tags[1] if len(tags) > 1 else None,
        }


def _detail_fields(page):
    title = _clean_text(_first(r'<h1\b[^>]*class="[^"]*\brd-career-detail__title\b[^"]*"[^>]*>(.*?)</h1>', page))
    meta = {}
    meta_html = _first(r'<dl\b[^>]*class="[^"]*\brd-career-detail__meta\b[^"]*"[^>]*>(.*?)</dl>', page)
    if meta_html:
        for item in re.findall(r"(?is)<div\b[^>]*\brd-career-detail__meta-item\b[^>]*>(.*?)</div>", meta_html):
            key = _clean_text(_first(r"(?is)<dt\b[^>]*>(.*?)</dt>", item))
            val = _clean_text(_first(r"(?is)<dd\b[^>]*>(.*?)</dd>", item))
            if key and val:
                meta[key.lower()] = val

    desc_html = None
    m = re.search(r'(?is)<div\b[^>]*class="[^"]*\brd-prose\b[^"]*\brd-career-detail__prose\b[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</section>', page)
    if m:
        desc_html = m.group(1)
    else:
        desc_html = _first(r'(?is)<div\b[^>]*class="[^"]*\brd-prose\b[^"]*\brd-career-detail__prose\b[^"]*"[^>]*>(.*?)</div>', page)

    return {
        "title": title,
        "location": meta.get("location"),
        "department": meta.get("job title"),
        "description": _clean_text(desc_html),
    }


def crawl(ctx):
    page = ctx.fetch(SITE)
    for card in _vacancy_cards(page):
        detail = _detail_fields(ctx.fetch(card["url"]))
        title = detail.get("title") or card.get("title")
        location = detail.get("location") or card.get("location")
        description = detail.get("description")
        if not (title and location and description):
            continue

        job = {
            "url": card["url"],
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }
        if card.get("employment_type"):
            job["employment_type"] = card["employment_type"]
        if detail.get("department"):
            job["department"] = detail["department"]
        yield job
