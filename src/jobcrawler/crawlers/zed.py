
import re
from urllib.parse import urljoin

SITE = "https://zed.dev/jobs"
EXTRA_HOSTS = []

def clean_text(fragment):
    if not fragment:
        return None
    fragment = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|section|article|li|ul|ol|h1|h2|h3|hr)>", "\n", fragment)
    fragment = re.sub(r"(?i)<li\b[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    entities = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#x27;": "'",
        "&#39;": "'",
        "&nbsp;": " ",
        "&mdash;": "-",
        "&ndash;": "-",
        "&rsquo;": "'",
        "&lsquo;": "'",
        "&rdquo;": '"',
        "&ldquo;": '"',
        "&rarr;": "->",
    }
    for old, new in entities.items():
        fragment = fragment.replace(old, new)
    fragment = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), fragment)
    fragment = re.sub(r"&#([0-9]+);", lambda m: chr(int(m.group(1))), fragment)
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    fragment = re.sub(r"\n\s*\n\s*", "\n\n", fragment)
    text = fragment.strip()
    return text or None

def find_one(pattern, text):
    match = re.search(pattern, text, re.I | re.S)
    return match.group(1) if match else None

def detail_to_job(ctx, url):
    page = ctx.fetch(url)

    title = clean_text(find_one(r"<h1\b[^>]*>(.*?)</h1>", page))
    if not title:
        return None

    h1_end = page.lower().find("</h1>")
    after_h1 = page[h1_end + 5:] if h1_end != -1 else page
    location = clean_text(find_one(r"<p\b[^>]*>(.*?)</p>", after_h1))

    start = page.find("<h1")
    if start == -1:
        start = 0
    end_positions = []
    for marker in ('<hr', 'href="/jobs#perks-and-benefits"', "</main>"):
        pos = page.find(marker, start)
        if pos != -1:
            end_positions.append(pos)
    end = min(end_positions) if end_positions else len(page)
    description = clean_text(page[start:end])

    apply_url = None
    apply_match = re.search(r'href="([^"]+)"[^>]*>\s*Apply for this position\s*</a>', page, re.I | re.S)
    if apply_match:
        apply_url = apply_match.group(1).replace("&amp;", "&")

    job = {
        "url": url,
        "title": title,
        "company": "Zed Industries",
        "location": location,
        "description": description,
        "source": SITE,
    }
    if apply_url:
        job["apply_url"] = apply_url
    return job

def crawl(ctx):
    listing = ctx.fetch(SITE)
    seen = set()
    for href in re.findall(r'href="(/jobs/[^"#?]+)"', listing):
        url = urljoin(SITE, href)
        if url in seen or url.rstrip("/") == SITE.rstrip("/"):
            continue
        seen.add(url)
        job = detail_to_job(ctx, url)
        if job:
            yield job
