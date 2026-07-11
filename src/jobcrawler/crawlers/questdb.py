
import re
import html
from urllib.parse import urljoin

SITE = "https://questdb.com/careers/"
EXTRA_HOSTS = []

def _strip_tags(s):
    s = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<svg\b[^>]*>.*?</svg>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|ul|ol)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

def _abs(url):
    return urljoin(SITE, html.unescape(url))

def _career_urls_from_sitemap(xml):
    urls = []
    for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml, flags=re.I):
        loc = html.unescape(loc.strip())
        if re.match(r"^https://questdb\.com/careers/[^/]+/$", loc):
            urls.append(loc)
    return sorted(dict.fromkeys(urls))

def _role_cards(home):
    out = {}
    for href, body in re.findall(r'(?is)<a\b[^>]*href="([^"]*/careers/[^"]+)"[^>]*>(.*?)</a>', home):
        url = _abs(href)
        if not re.match(r"^https://questdb\.com/careers/[^/]+/$", url):
            continue
        team = title = location = employment_type = None
        m = re.search(r'(?is)<div\b[^>]*class="[^"]*page_team[^"]*"[^>]*>(.*?)</div>', body)
        if m:
            team = _strip_tags(m.group(1)) or None
        m = re.search(r'(?is)<div\b[^>]*class="[^"]*page_roleTitle[^"]*"[^>]*>(.*?)</div>', body)
        if m:
            title = _strip_tags(m.group(1)) or None
        spans = [_strip_tags(x) for x in re.findall(r"(?is)<span\b[^>]*>(.*?)</span>", body)]
        spans = [x for x in spans if x and x != "->" and x != "→"]
        if spans:
            location = spans[0]
        if len(spans) > 1:
            employment_type = spans[1]
        out[url] = {
            "title": title,
            "department": team,
            "location": location,
            "employment_type": employment_type,
        }
    return out

def _first(pattern, text, flags=re.I|re.S):
    m = re.search(pattern, text, flags)
    return html.unescape(m.group(1)).strip() if m else None

def _job_from_page(url, page, card):
    title = card.get("title") or _first(r"<h1[^>]*>(.*?)</h1>", page)
    if not title:
        title = _first(r"<title[^>]*>(.*?)</title>", page)
        if title:
            title = re.sub(r"\s*\|\s*Careers at QuestDB\s*\|\s*QuestDB\s*$", "", title).strip()

    apply_url = _first(r'href="(https://jobs\.gem\.com/questdb-com/[^"]+)"', page)

    desc_html = None
    m = re.search(r'(?is)<div\b[^>]*class="[^"]*leading-relaxed[^"]*"[^>]*>(.*?)(?:<a\b[^>]*href="https://jobs\.gem\.com|</article>)', page)
    if m:
        desc_html = m.group(1)
    else:
        m = re.search(r"(?is)<article\b[^>]*>(.*?)</article>", page)
        desc_html = m.group(1) if m else page
    description = _strip_tags(desc_html)

    item = {
        "url": url,
        "title": title,
        "company": "QuestDB",
        "location": card.get("location") or "London, New York or Remote",
        "description": description,
        "source": SITE,
    }
    if card.get("department"):
        item["department"] = card["department"]
    if card.get("employment_type"):
        item["employment_type"] = card["employment_type"]
    if apply_url:
        item["apply_url"] = apply_url
    return item

def crawl(ctx):
    home = ctx.fetch(SITE)
    cards = _role_cards(home)

    sitemap = ctx.fetch("https://questdb.com/sitemap-0.xml")
    urls = _career_urls_from_sitemap(sitemap)

    # Cross-check the sitemap against the rendered board so a stale sitemap does not under-enumerate.
    for url in cards:
        if url not in urls:
            urls.append(url)

    for url in sorted(dict.fromkeys(urls)):
        page = ctx.fetch(url)
        yield _job_from_page(url, page, cards.get(url, {}))
