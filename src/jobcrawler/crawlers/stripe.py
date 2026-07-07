
import re
import html
from urllib.parse import urljoin

SITE = "https://stripe.com/jobs/search"
EXTRA_HOSTS = []

ROOT_SITEMAP = "https://stripe.com/sitemap/sitemap.xml"
MAX_JOBS = 25

def _strip_tags(value):
    if not value:
        return None
    value = re.sub(r"(?is)<script\b.*?</script>|<style\b.*?</style>|<svg\b.*?</svg>", " ", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</(p|div|li|h[1-6]|ul|ol)>", "\n", value)
    value = re.sub(r"(?is)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None

def _attr(page, pattern):
    m = re.search(pattern, page, re.I | re.S)
    return html.unescape(m.group(1)).strip() if m else None

def _canonical(url):
    return re.sub(r"/apply/?$", "", url)

def _listing_urls(ctx):
    root = ctx.fetch(ROOT_SITEMAP)
    parts = re.findall(r"<loc>(https://stripe\.com/sitemap/partition-\d+\.xml)</loc>", root)
    if not parts:
        parts = [ROOT_SITEMAP]
    seen = set()
    for part in parts:
        xml = ctx.fetch(part)
        for loc in re.findall(r"<loc>(https://stripe\.com/jobs/listing/[^<]+)</loc>", xml):
            loc = _canonical(html.unescape(loc))
            if loc not in seen and re.search(r"/\d+$", loc):
                seen.add(loc)
                yield loc

def _properties(page):
    props = {}
    for block in re.findall(r'(?is)<div class="JobDetailCardProperty">(.*?)(?=<div class="JobDetailCardProperty"|<div class="JobDetailCard__buttonContainer"|</aside>|</section>)', page):
        title = _strip_tags(_attr(block, r'(?is)<p class="JobDetailCardProperty__title">(.*?)</p>') or "")
        ps = re.findall(r"(?is)<p(?:\s[^>]*)?>(.*?)</p>", block)
        if title and len(ps) >= 2:
            value = _strip_tags(ps[-1])
            if value:
                props[title.lower()] = value
    return props

def _description(page):
    m = re.search(r'(?is)<div class="ArticleMarkdown">(.*?)(?=<div class="JobDetailCard\b|<aside\b|<footer\b)', page)
    if not m:
        m = re.search(r'(?is)<div class="ArticleMarkdown">(.*?)</div>\s*</div>\s*</div>', page)
    if not m:
        m = re.search(r'(?is)<main[^>]*>(.*?)</main>', page)
    return _strip_tags(m.group(1) if m else page)

def _parse_job(url, page):
    title = _attr(page, r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)') or _attr(page, r"<title[^>]*>(.*?)</title>")
    if title:
        title = re.sub(r"\s*\|\s*Stripe\s*$", "", title).strip()

    props = _properties(page)
    locations = []
    for key in ("office locations", "remote locations"):
        if props.get(key):
            locations.append(props[key])
    location = "; ".join(locations) or props.get("location") or "Not specified"

    apply_match = re.search(r'href=["\']([^"\']*/jobs/listing/[^"\']+/apply)["\']', page)
    apply_url = urljoin(url, html.unescape(apply_match.group(1))) if apply_match else None

    description = _description(page)
    external_id = None
    m = re.search(r"/(\d+)/?$", url)
    if m:
        external_id = m.group(1)

    job = {
        "url": url,
        "title": title,
        "company": "Stripe",
        "location": location,
        "description": description,
        "source": SITE,
    }
    if props.get("team"):
        job["department"] = props["team"]
    if props.get("job type"):
        job["employment_type"] = props["job type"]
    if props.get("remote locations"):
        job["workplace_type"] = "Remote"
    if apply_url:
        job["apply_url"] = apply_url
    if external_id:
        job["external_id"] = external_id
    return job

def crawl(ctx):
    count = 0
    for url in _listing_urls(ctx):
        if count >= MAX_JOBS:
            break
        page = ctx.fetch(url)
        job = _parse_job(url, page)
        if job.get("title") and job.get("description"):
            count += 1
            yield job
