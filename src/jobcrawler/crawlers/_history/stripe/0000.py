
import html
import re
from urllib.parse import urljoin

SITE = "https://stripe.com/jobs/search"
EXTRA_HOSTS = []
SITEMAP_URL = "https://stripe.com/sitemap/partition-0.xml"

def _text_from_html(fragment):
    fragment = re.sub(r"(?is)<(script|style|svg)[^>]*>.*?</\1>", " ", fragment or "")
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|li|h1|h2|h3|section|ul|ol)>", "\n", fragment)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", fragment))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _first(pattern, text, flags=re.I | re.S):
    m = re.search(pattern, text or "", flags)
    return html.unescape(m.group(1)).strip() if m else None

def _field_from_plain(plain, label, next_labels):
    labels = "|".join(re.escape(x) for x in next_labels)
    m = re.search(re.escape(label) + r"\s+(.+?)(?=\s+(?:" + labels + r")\s+|$)", plain)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None

def _main_description(page):
    start = page.find("<main")
    if start < 0:
        start = 0
    end = page.find("JobsDetailCard", start)
    if end < 0:
        end = page.find("Apply Now", start)
    fragment = page[start:end if end > start else len(page)]
    text = _text_from_html(fragment)
    title = _first(r"<title[^>]*>(.*?)</title>", page) or ""
    if title and title in text:
        text = text[text.find(title):]
    return text.strip()

def _extract_job(ctx, url):
    page = ctx.fetch(url)
    title = _first(r"<title[^>]*>(.*?)</title>", page)
    canonical = _first(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', page) or url
    apply_href = _first(r'<a[^>]+href=["\']([^"\']*/apply)["\'][^>]*>\s*Apply Now', page)
    plain = _text_from_html(page)
    labels = ["Office locations", "Remote locations", "Team", "Job type", "Apply for this role"]
    office = _field_from_plain(plain, "Office locations", labels)
    remote = _field_from_plain(plain, "Remote locations", labels)
    team = _field_from_plain(plain, "Team", labels)
    job_type = _field_from_plain(plain, "Job type", labels)
    location = "; ".join(x for x in (office, remote) if x) or None
    description = _main_description(page)

    job = {
        "url": canonical,
        "title": title,
        "company": "Stripe",
        "location": location,
        "description": description,
        "source": SITE,
    }
    if team:
        job["department"] = team
    if job_type:
        job["employment_type"] = job_type
    if apply_href:
        job["apply_url"] = urljoin(canonical, apply_href)
    m = re.search(r"/(\d+)(?:/)?$", canonical)
    if m:
        job["external_id"] = m.group(1)
    sm = re.search(r"(?:annual [^.]*salary range[^$]*)(\$[\d,]+\s*-\s*\$[\d,]+)", description or "", re.I)
    if sm:
        job["salary"] = sm.group(1)
    if "Hybrid work at Stripe" in (description or ""):
        job["workplace_type"] = "Hybrid"
    elif remote:
        job["workplace_type"] = "Remote"
    return job

def crawl(ctx):
    xml = ctx.fetch(SITEMAP_URL)
    seen = set()
    count = 0
    for loc in re.findall(r"<loc>(.*?)</loc>", xml):
        loc = html.unescape(loc).strip()
        if not re.search(r"/jobs/listing/[^/]+/\d+$", loc) or loc in seen:
            continue
        seen.add(loc)
        job = _extract_job(ctx, loc)
        if job.get("title") and job.get("location") and job.get("description"):
            yield job
            count += 1
        if count >= 35:
            break
