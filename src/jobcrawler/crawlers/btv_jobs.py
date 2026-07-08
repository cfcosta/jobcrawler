
import re
import html
from urllib.parse import urljoin

SITE = "https://jobs.btv.vc/jobs"
EXTRA_HOSTS = []

SITEMAP_URL = "https://jobs.btv.vc/sitemap.xml"
MAX_JOBS = 3


def _clean_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t\r\n-|")


def _strip_tags(fragment):
    fragment = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", fragment or "")
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|section|article|ul|ol)>", "\n", fragment)
    text = re.sub(r"(?s)<[^>]+>", " ", fragment)
    text = html.unescape(text)
    lines = []
    for line in re.split(r"[\r\n]+", text):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def _tag_end(html_text, start):
    m = re.match(r"(?is)<([a-z0-9]+)\b", html_text[start:])
    if not m:
        return -1
    tag = m.group(1).lower()
    depth = 0
    token_re = re.compile(r"(?is)</?%s\b[^>]*>" % re.escape(tag))
    for tm in token_re.finditer(html_text, start):
        tok = tm.group(0)
        if tok.startswith("</"):
            depth -= 1
            if depth == 0:
                return tm.end()
        elif not tok.rstrip().endswith("/>"):
            depth += 1
    return -1


def _extract_title_company(page):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    title_text = _clean_text(re.sub(r"(?s)<[^>]+>", " ", m.group(1))) if m else ""
    title_text = re.sub(r"\s*\|\s*Better Tomorrow Ventures.*$", "", title_text).strip()
    if " @ " in title_text:
        title, company = title_text.rsplit(" @ ", 1)
        return _clean_text(title), _clean_text(company)
    return _clean_text(title_text), "Better Tomorrow Ventures"


def _extract_description(page):
    m = re.search(r'(?is)<([a-z0-9]+)\b[^>]*class="[^"]*\bcrBMPy\b[^"]*"[^>]*>', page)
    if m:
        end = _tag_end(page, m.start())
        if end > m.start():
            text = _strip_tags(page[m.start():end])
            if len(text) > 80:
                return text

    body = re.sub(r"(?is)<head.*?</head>", " ", page)
    text = _strip_tags(body)
    # Trim leading site chrome if the description container was not found.
    markers = [
        "About the role", "About this role", "The role", "Responsibilities",
        "What you'll do", "What you will do", "Quem somos", "Sobre a vaga",
        "Sobre o Clubbi", "About Us", "About the company"
    ]
    lower = text.lower()
    cuts = [lower.find(m.lower()) for m in markers if lower.find(m.lower()) != -1]
    if cuts:
        text = text[min(cuts):]
    text = re.sub(r"(?is)\n?Apply for this job.*$", "", text).strip()
    return text


def _extract_header_text(page):
    body = re.sub(r"(?is)<head.*?</head>", " ", page)
    text = _strip_tags(body)
    return text[:4000]


def _extract_location(page, title, company):
    head = _extract_header_text(page)
    for pat in [
        r"(?:Location|Localizacao|Localização)\s*[:\-]\s*([^\n|]+)",
        r"\b([A-Z][A-Za-z .'-]+,\s*[A-Z]{2}(?:,\s*United States)?)\b",
        r"\b(Remote(?:\s*[-,]\s*[A-Za-z ]+)?)\b",
        r"\b(Brazil|Brasil|Canada|United States|Mexico|Colombia|India|United Kingdom)\b",
    ]:
        m = re.search(pat, head, re.I)
        if m:
            return _clean_text(m.group(1))
    # Getro header text often contains short metadata lines after title/company.
    lines = [_clean_text(x) for x in head.split("\n") if _clean_text(x)]
    bad = {title.lower(), company.lower(), "jobs", "companies", "talent network", "apply"}
    for line in lines:
        low = line.lower()
        if low in bad or "job board" in low or len(line) > 90:
            continue
        if any(tok in low for tok in ["remote", "hybrid", "brazil", "brasil", "canada", "united", "new york", "san francisco", "toronto", "london", "mexico"]):
            return line
    return None


def _extract_apply_url(page, url):
    for href in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\']', page):
        h = html.unescape(href)
        low = h.lower()
        if "apply" in low or "lever.co" in low or "greenhouse.io" in low or "ashbyhq.com" in low:
            return urljoin(url, h)
    return None


def _extract_employment_type(page):
    text = _extract_header_text(page)
    for val in ["Full-time", "Part-time", "Contract", "Internship", "Temporary"]:
        if re.search(r"\b" + re.escape(val) + r"\b", text, re.I):
            return val
    return None


def _extract_workplace_type(page):
    text = _extract_header_text(page)
    for val in ["Remote", "Hybrid", "On-site", "Onsite"]:
        if re.search(r"\b" + re.escape(val) + r"\b", text, re.I):
            return "On-site" if val == "Onsite" else val
    return None


def crawl(ctx):
    sitemap = ctx.fetch(SITEMAP_URL)
    seen = set()
    urls = []
    for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", sitemap):
        loc = html.unescape(loc).split("#", 1)[0]
        if "/companies/" in loc and "/jobs/" in loc and loc not in seen:
            seen.add(loc)
            urls.append(loc)
            if len(urls) >= MAX_JOBS:
                break

    for url in urls:
        page = ctx.fetch(url)
        title, company = _extract_title_company(page)
        description = _extract_description(page)
        if not title or not company or len(description) < 50:
            continue
        location = _extract_location(page, title, company)
        if not location:
            continue
        job = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }
        ext = re.search(r"/jobs/(\d+)", url)
        if ext:
            job["external_id"] = ext.group(1)
        apply_url = _extract_apply_url(page, url)
        if apply_url:
            job["apply_url"] = apply_url
        emp = _extract_employment_type(page)
        if emp:
            job["employment_type"] = emp
        workplace = _extract_workplace_type(page)
        if workplace:
            job["workplace_type"] = workplace
        yield job
