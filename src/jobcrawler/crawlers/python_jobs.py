
import re
import html
from urllib.parse import urljoin

SITE = "https://www.python.org/jobs/"
EXTRA_HOSTS = []


def _text(s):
    if not s:
        return None
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    return s or None


def _first(pattern, text, flags=re.S | re.I):
    m = re.search(pattern, text, flags)
    return m.group(1) if m else None


def _listing_urls(page_html):
    seen = set()
    urls = []
    for href in re.findall(r'href=["\'](/jobs/\d+/)["\']', page_html):
        if href not in seen:
            seen.add(href)
            urls.append(urljoin(SITE, href))
    return urls


def _next_url(page_html, current_url):
    m = re.search(r'<li class=["\']next["\']>\s*<a([^>]*)href=["\']([^"\']*)["\']', page_html, re.I | re.S)
    if not m:
        return None
    attrs, href = m.group(1), m.group(2)
    if "disabled" in attrs or not href:
        return None
    return urljoin(current_url, html.unescape(href))


def _parse_detail(url, body):
    article = _first(r'(<article class=["\']text["\'].*?</article>)', body) or body
    header = _first(r'<h1 class=["\']listing-company["\']>(.*?)</h1>', article) or ""
    header = re.sub(r'<span class=["\']listing-new["\']>.*?</span>', '', header, flags=re.I | re.S)

    name_block = _first(r'<span class=["\']company-name["\']>(.*?)</span>', header) or ""
    name_lines = [x.strip() for x in (_text(name_block) or "").splitlines() if x.strip()]
    title = name_lines[0] if name_lines else None
    company = name_lines[-1] if len(name_lines) > 1 else None

    location_html = _first(r'<span class=["\']listing-location["\']>(.*?)</span>', header) or ""
    location = _text(location_html)

    desc_html = _first(r'<div class=["\']job-description["\']>(.*?)(?:</div>\s*<!-- end \.job-post-content -->|<div class=["\']job-post-meta["\']>)', article)
    description = _text(desc_html)

    posted_at = _first(r'<time[^>]+datetime=["\']([^"\']+)["\']', article)
    dept_html = _first(r'<span class=["\']listing-company-category["\']>(.*?)</span>', article)
    department = _text(dept_html)
    types_html = _first(r'<span class=["\']listing-job-type["\']>(.*?)</span>', article)
    job_types = _text(types_html)

    # The Contact Info block gives up to three channels: a contact name, an
    # e-mail, and an optional "Web" link — the employer's real application page
    # (e.g. jobs.micro1.ai/post/…). Prefer the Web link; the mailto is the apply
    # target only when it is all the posting offers. Board self-links are
    # skipped so a stray python.org href can't masquerade as the employer's.
    apply_url = None
    m = re.search(
        r'<li>\s*<strong>\s*Web\s*</strong>\s*:\s*<a[^>]+href=["\'](https?://[^"\']+)["\']',
        desc_html or "",
        re.I,
    )
    if m:
        url_candidate = html.unescape(m.group(1))
        if "python.org" not in url_candidate.lower():
            apply_url = url_candidate
    if not apply_url:
        m = re.search(r'<a[^>]+href=["\'](mailto:[^"\']+)["\']', desc_html or "", re.I)
        if m:
            apply_url = html.unescape(m.group(1))

    external_id = None
    m = re.search(r'/jobs/(\d+)/', url)
    if m:
        external_id = m.group(1)

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "source": SITE,
    }
    optional = {
        "posted_at": posted_at,
        "department": department or job_types,
        "external_id": external_id,
        "apply_url": apply_url,
    }
    for key, value in optional.items():
        if value:
            job[key] = value
    return job


def crawl(ctx):
    page_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page_html = ctx.fetch(page_url)

        for job_url in _listing_urls(page_html):
            if job_url in seen_jobs:
                continue
            seen_jobs.add(job_url)
            detail_html = ctx.fetch(job_url)
            job = _parse_detail(job_url, detail_html)
            if job.get("title") and job.get("company") and job.get("location") and job.get("description"):
                yield job

        page_url = _next_url(page_html, page_url)
