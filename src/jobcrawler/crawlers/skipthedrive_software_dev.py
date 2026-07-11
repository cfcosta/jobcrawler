
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://www.skipthedrive.com/job-category/remote-software-development-jobs/"
EXTRA_HOSTS = []
MAX_PAGES = 3

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _first_match(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text or "", flags)
    return m.group(1).strip() if m else None

def _page_url(page):
    return SITE if page <= 1 else urljoin(SITE, "page/%d/" % page)

def _listing_jobs(listing_html):
    seen = set()
    pattern = r"<a[^>]+href=[\"']([^\"']+/job/[^\"']+)[\"']"
    for href in re.findall(pattern, listing_html or "", re.I):
        url = html.unescape(href).split("#", 1)[0]
        if "/job/sponsored-job-" in url:
            continue
        m = re.search(r"-(\d+)/?$", url)
        if not m:
            continue
        job_id = m.group(1)
        if job_id not in seen:
            seen.add(job_id)
            yield job_id, url

def _company_from_excerpt(excerpt_html):
    company_html = _first_match(r"custom_fields_company_name_display_search_results[^>]*>(.*?)</span>", excerpt_html)
    if company_html:
        return _clean_html(company_html)
    text = _clean_html(excerpt_html) or ""
    m = re.match(r"(.+?)\s+(?:\d+\+?\s+days?\s+ago|Yesterday|Today)\b", text, re.I)
    return m.group(1).strip() if m else None

def _apply_url(content_html):
    for m in re.finditer(r"<a\b([^>]*?)>(.*?)</a>", content_html or "", re.I | re.S):
        attrs, label_html = m.group(1), m.group(2)
        label = (_clean_html(label_html) or "").lower()
        if "apply" not in label:
            continue
        href = _first_match(r"href=[\"']([^\"']+)[\"']", attrs, re.I)
        if href:
            return html.unescape(href)
    return None

def _job_from_record(data, fallback_url):
    title = _clean_html((data.get("title") or {}).get("rendered"))
    content_html = (data.get("content") or {}).get("rendered") or ""
    description = _clean_html(content_html)
    excerpt_html = (data.get("excerpt") or {}).get("rendered") or ""
    company = _company_from_excerpt(excerpt_html)
    url = data.get("link") or fallback_url

    if not (title and description and company and url):
        return None

    job = {
        "url": url,
        "title": title,
        "company": company,
        "location": "Remote",
        "description": description,
        "source": SITE,
        "external_id": str(data.get("id")),
        "workplace_type": "Remote",
    }

    posted_at = data.get("date_gmt") or data.get("date")
    if posted_at:
        job["posted_at"] = posted_at

    apply_url = _apply_url(content_html)
    if apply_url:
        job["apply_url"] = apply_url

    return {k: v for k, v in job.items() if v is not None and v != ""}

def _fetch_batch(ctx, ids):
    url = "https://www.skipthedrive.com/wp-json/wp/v2/job?per_page=100&include=" + ",".join(ids)
    records = json.loads(ctx.fetch(url))
    return records if isinstance(records, list) else []

def crawl(ctx):
    seen_ids = set()
    fallback_urls = {}

    for page in range(1, MAX_PAGES + 1):
        listing = ctx.fetch(_page_url(page))
        pairs = [(job_id, url) for job_id, url in _listing_jobs(listing) if job_id not in seen_ids]
        if not pairs:
            break

        ids = []
        for job_id, url in pairs:
            seen_ids.add(job_id)
            fallback_urls[job_id] = url
            ids.append(job_id)

        for record in _fetch_batch(ctx, ids):
            job_id = str(record.get("id") or "")
            job = _job_from_record(record, fallback_urls.get(job_id))
            if job:
                yield job
