
import re
import html
from urllib.parse import urljoin

SITE = "https://vrsoftware.rhgestor.com.br/vagas"
EXTRA_HOSTS = []

def clean(s):
    s = re.sub(r"(?i)<br\s*/?>", "\n", s or "")
    s = re.sub(r"(?i)</(h[1-6]|div|p|li)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() or None

def first(pat, txt):
    m = re.search(pat, txt, re.I | re.S)
    return clean(m.group(1)) if m else None

def crawl(ctx):
    listing = ctx.fetch(SITE)
    entries = []
    seen = set()

    for m in re.finditer(r'href=["\']([^"\']*/vagas/detalhes/[^"\']+)["\']', listing, re.I):
        url = urljoin(SITE, html.unescape(m.group(1)))
        if url in seen:
            continue
        seen.add(url)

        block = listing[m.start():m.start() + 1200]
        title = first(r'<strong[^>]*>(.*?)</strong>', block)
        location = first(r'<div[^>]*localizacao-vaga[^>]*>(.*?)</div>', block)
        entries.append((url, title, location))

    for url, list_title, list_location in entries:
        page = ctx.fetch(url)
        title = first(r"<h1[^>]*>(.*?)</h1>", page) or list_title
        if not title:
            title = first(r"<title[^>]*>(.*?)</title>", page)
            if title:
                title = re.sub(r"\s*-\s*VR Software\s*$", "", title).strip()

        items = [
            clean(x)
            for x in re.findall(
                r'<div[^>]+class=["\'][^"\']*vaga-item[^"\']*["\'][^>]*>(.*?)</div>',
                page,
                re.I | re.S,
            )
        ]
        items = [x for x in items if x]
        description = "\n\n".join(items) or title

        location = list_location or "Limeira - SP"
        employment_type = None
        for item in items:
            lines = [ln.strip() for ln in item.splitlines() if ln.strip()]
            if not lines:
                continue
            heading = lines[0].lower()
            if heading.startswith("local do trabalho") and len(lines) > 1:
                location = lines[1].replace(" | ", " - ").strip()
            elif heading.startswith("regime de contratação") and len(lines) > 1:
                employment_type = lines[1].strip()

        job = {
            "url": url,
            "title": title,
            "company": "VR Software",
            "location": location,
            "description": description,
            "source": SITE,
        }
        if employment_type:
            job["employment_type"] = employment_type

        yield job
