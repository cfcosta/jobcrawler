
import html
import re
from urllib.parse import urlparse

SITE = "https://www.getonbrd.world/"
EXTRA_HOSTS = []


def _clean_text(value):
    if not value:
        return None
    value = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|ul|ol)>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def _first(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text or "", flags)
    return m.group(1) if m else None


def _world_url(raw_url):
    raw_url = html.unescape(raw_url or "")
    raw_url = re.sub(r"[?#].*$", "", raw_url)
    parsed = urlparse(raw_url)
    if parsed.netloc == "www.getonbrd.com" and re.match(r"^/(jobs|empleos)/", parsed.path):
        return "https://www.getonbrd.world" + parsed.path
    if parsed.netloc == "www.getonbrd.world" and re.match(r"^/(jobs|empleos)/", parsed.path):
        return raw_url
    return None


def _parse_cards(listing_html):
    seen = set()
    pattern = r'<a\b(?=[^>]*\bclass=["\'][^"\']*results-item[^"\']*["\'])[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
    for m in re.finditer(pattern, listing_html or "", re.I | re.S):
        url = _world_url(m.group(1))
        if not url or url in seen:
            continue
        seen.add(url)
        body = m.group(2)

        title = _clean_text(_first(r'<h4[^>]*class=["\'][^"\']*results-list-title[^"\']*["\'][^>]*>\s*<strong[^>]*>(.*?)</strong>', body))
        company_block = _first(r'<div[^>]*class=["\'][^"\']*size0[^"\']*["\'][^>]*>(.*?)</div>', body)
        company = _clean_text(_first(r"<strong[^>]*>(.*?)</strong>", company_block or ""))
        location = _clean_text(_first(r'<span[^>]*class=["\']location["\'][^>]*>(.*?)</span>\s*</span>', body))
        if not location:
            location = _clean_text(_first(r'<span[^>]*class=["\']location["\'][^>]*>(.*?)</span>', body))

        employment_type = None
        h4_text = _clean_text(_first(r'<h4[^>]*class=["\'][^"\']*results-list-title[^"\']*["\'][^>]*>(.*?)</h4>', body)) or ""
        for label in ("Full time", "Part time", "Freelance", "Práctica", "Internship"):
            if label.lower() in h4_text.lower():
                employment_type = label
                break

        salary = None
        money = re.search(r'<i[^>]+icon-money-bill[^>]*>.*?</i>\s*<span>(.*?)</span>', body, re.I | re.S)
        if money:
            salary = _clean_text(money.group(1))

        yield {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "employment_type": employment_type,
            "salary": salary,
        }


def _parse_detail(url, text, card):
    title = _clean_text(_first(r'<span[^>]*itemprop=["\']title["\'][^>]*>(.*?)</span>', text)) or card.get("title")
    company = _clean_text(_first(r'<strong[^>]*itemprop=["\']name["\'][^>]*>(.*?)</strong>', text)) or card.get("company")
    location = _clean_text(_first(r'<span[^>]*class=["\']location["\'][^>]*>(.*?)</span>\s*</span>', text)) or card.get("location")
    if not location:
        location = _clean_text(_first(r'<span[^>]*class=["\']location["\'][^>]*>(.*?)</span>', text)) or card.get("location")

    body = _first(r'<div[^>]*id=["\']job-body["\'][^>]*>(.*?)(?:<div[^>]+id=["\']js-apply-section["\']|<div[^>]+id=["\']jobs-navigation["\'])', text)
    description = _clean_text(body)

    if not (url and title and company and location and description):
        return None

    kwargs = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }

    posted_at = _first(r'<time[^>]+datetime=["\']([^"\']+)["\'][^>]*itemprop=["\']datePosted["\']', text)
    employment_type = _clean_text(_first(r'<span[^>]*itemprop=["\']employmentType["\'][^>]*>(.*?)</span>', text)) or card.get("employment_type")
    department = _clean_text(_first(r'<a[^>]+class=["\']color-inherit["\'][^>]*href=["\']/jobs/[^"\']+["\'][^>]*>(.*?)</a>', text))
    apply_url = _first(r'<a[^>]+id=["\']apply_bottom["\'][^>]+href=["\']([^"\']+)["\']', text)
    external_id = urlparse(url).path.rstrip("/").split("/")[-1] or None

    optional = {
        "salary": card.get("salary"),
        "employment_type": employment_type,
        "workplace_type": "remote" if location and "remoto" in location.lower() else None,
        "department": department,
        "posted_at": posted_at,
        "external_id": external_id,
        "apply_url": html.unescape(apply_url) if apply_url else None,
    }
    for key, value in optional.items():
        if value:
            kwargs[key] = value
    return kwargs


def crawl(ctx):
    listing_html = ctx.fetch(SITE)
    yielded = 0
    for card in _parse_cards(listing_html):
        if yielded >= 12:
            break
        try:
            detail_html = ctx.fetch(card["url"])
        except Exception:
            continue
        job = _parse_detail(card["url"], detail_html, card)
        if job:
            yielded += 1
            yield job
