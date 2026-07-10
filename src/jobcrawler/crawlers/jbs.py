
import json
import re
import html
from urllib.parse import urljoin

SITE = "https://grupojbs.gupy.io/"
EXTRA_HOSTS = []
MAX_JOBS = 40

def _next_data(text):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.S)
    return json.loads(html.unescape(m.group(1))) if m else {}

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _location(job, lite):
    parts = []
    for key in ("addressCity", "addressState", "addressCountry"):
        value = job.get(key)
        if value and value not in parts:
            parts.append(value)
    if not parts:
        addr = (lite.get("workplace") or {}).get("address") or {}
        for key in ("city", "state", "country"):
            value = addr.get(key)
            if value and value not in parts:
                parts.append(value)
    return ", ".join(parts) if parts else "Brasil"

def _body(job):
    sections = []
    for key, label in (
        ("description", None),
        ("responsibilities", "Responsabilidades"),
        ("prerequisites", "Requisitos"),
        ("relevantExperiences", "Informacoes adicionais"),
    ):
        text = _clean_html(job.get(key))
        if text:
            sections.append((label + "\n" if label else "") + text)
    return "\n\n".join(sections).strip()

def _add(out, key, value):
    if value is not None and value != "":
        out[key] = value

def crawl(ctx):
    root = _next_data(ctx.fetch(SITE))
    jobs = root.get("props", {}).get("pageProps", {}).get("jobs") or []

    for lite in jobs[:MAX_JOBS]:
        job_id = lite.get("id")
        if not job_id:
            continue
        url = urljoin(SITE, "jobs/%s?jobBoardSource=gupy_public_page" % job_id)
        page = _next_data(ctx.fetch(url))
        props = page.get("props", {}).get("pageProps", {})
        job = props.get("job") or {}
        title = job.get("name") or lite.get("title")
        description = _body(job)
        if not title or not description:
            continue

        item = {
            "url": props.get("canonicalUrl") or url,
            "title": title,
            "company": "JBS",
            "location": _location(job, lite),
            "description": description,
            "source": SITE,
        }
        _add(item, "external_id", str(job.get("id") or job_id))
        _add(item, "department", lite.get("department"))
        _add(item, "employment_type", job.get("jobType") or lite.get("type"))
        _add(item, "workplace_type", job.get("workplaceType") or ((lite.get("workplace") or {}).get("workplaceType")))
        _add(item, "posted_at", job.get("publishedAt"))
        _add(item, "expires_at", job.get("expiresAt") or job.get("registerEndDate"))
        _add(item, "apply_url", item["url"])
        yield item
