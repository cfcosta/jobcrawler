
import json
import re
from html import unescape

SITE = "https://remotehabits.com/jobs/"
EXTRA_HOSTS = []


def _fix_text(text):
    if not isinstance(text, str):
        return text
    text = text.replace("\u00c2\u00a0", " ").replace("\xa0", " ")
    # RemoteOK reposts occasionally contain UTF-8 text decoded as Latin-1.
    if "Ã" in text or "Â" in text or "â" in text:
        try:
            repaired = text.encode("latin1").decode("utf-8")
            if len(repaired.strip()) >= len(text.strip()) * 0.8:
                text = repaired
        except Exception:
            pass
    return text


def _strip_html(value):
    if not value:
        return None
    text = _fix_text(unescape(str(value)))
    text = text.replace("\\/", "/")
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h1|h2|h3|h4|tr)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _fix_text(unescape(text))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _json_ld_blocks(html):
    pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for match in re.finditer(pattern, html, re.I | re.S):
        raw = unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                yield item


def _job_links(html):
    seen = set()
    pattern = r'<a[^>]+class=["\'][^"\']*\binternal-job-link\b[^"\']*["\'][^>]+href=["\']([^"\']+)["\']'
    for match in re.finditer(pattern, html, re.I):
        url = unescape(match.group(1))
        if url.startswith("/"):
            url = "https://remotehabits.com" + url
        if url not in seen:
            seen.add(url)
            yield url


def _as_text(value):
    if isinstance(value, str):
        value = _fix_text(value).strip()
        return value or None
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return _fix_text(name).strip()
    return None


def _page_url(page):
    return SITE if page == 1 else SITE + "?page=%d" % page


def crawl(ctx):
    # Each listing page embeds full JobPosting JSON-LD; no per-posting fetches are needed.
    for page in range(1, 50):
        html = ctx.fetch(_page_url(page))
        postings = list(_json_ld_blocks(html))
        links = list(_job_links(html))
        if not postings:
            break

        for idx, posting in enumerate(postings):
            title = _as_text(posting.get("title"))
            description = _strip_html(posting.get("description"))
            url = links[idx] if idx < len(links) else None
            org = posting.get("hiringOrganization") or {}
            company = _as_text(org) or "Remote Habits"

            loc = _as_text(posting.get("applicantLocationRequirements")) or _as_text(posting.get("jobLocation"))
            if not loc and posting.get("jobLocationType"):
                loc = _fix_text(str(posting.get("jobLocationType"))).strip()

            if not (url and title and description and company and loc):
                continue

            job = {
                "url": url,
                "title": title,
                "company": company,
                "location": loc,
                "description": description,
                "source": SITE,
            }

            employment_type = _as_text(posting.get("employmentType"))
            if employment_type:
                job["employment_type"] = employment_type

            workplace_type = _as_text(posting.get("jobLocationType"))
            if workplace_type:
                job["workplace_type"] = workplace_type

            posted_at = _as_text(posting.get("datePosted"))
            if posted_at:
                job["posted_at"] = posted_at

            expires_at = _as_text(posting.get("validThrough"))
            if expires_at:
                job["expires_at"] = expires_at

            yield job

        if len(postings) < 100:
            break
