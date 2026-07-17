"""Crawler for Zoobean / Beanstack (Joyful Reading Co.) first-party job pages."""

import html
import re

# Zoobean (Beanstack / Joyful Reading Co.) posts each opening as a static
# HubSpot page. The careers index links them across two first-party hosts —
# engineering roles live on beanstack.com, the rest on joyfulreading.com — and
# both are crawlable (robots Allow: /). There is no JSON feed, so enumerate the
# index and parse each rendered job page. `beanstack.com` in EXTRA_HOSTS also
# covers the `www.` subdomain the index actually links to (§8 on-domain match).
SITE = "https://joyfulreading.com/who-we-are/careers"
EXTRA_HOSTS = ["beanstack.com"]

COMPANY = "Zoobean"

_JOB_LINK_RE = re.compile(
    r'href="(https?://(?:www\.)?(?:beanstack|joyfulreading)\.com/jobs/[a-z0-9][a-z0-9-]*)"',
    re.I,
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property="og:title"[^>]+content="(.*?)"', re.I | re.S
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_LOCATION_RE = re.compile(r"masthead__location-text[^>]*>(.*?)</span>", re.I | re.S)
_ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.I | re.S)
_TITLE_SUFFIX_RE = re.compile(
    r"\s*[-–|]\s*(?:Zoobean|Beanstack|Joyful Reading(?: Co\.| Company)?)\s*$",
    re.I,
)


def _text(fragment):
    if not fragment:
        return ""
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", fragment)
    fragment = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", fragment)
    fragment = re.sub(
        r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|section|article|tr)\s*>", "\n", fragment
    )
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    fragment = re.sub(r"[ \t\r\f\v]+", " ", fragment)
    fragment = re.sub(r" *\n *", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()


def _clean_title(raw):
    raw = html.unescape(raw or "").strip()
    return _TITLE_SUFFIX_RE.sub("", raw).strip()


def crawl(ctx):
    """Yield one Job dict per opening listed on the Zoobean careers index."""
    index = ctx.fetch(SITE)

    seen = set()
    job_urls = []
    for match in _JOB_LINK_RE.finditer(index):
        url = match.group(1)
        if url not in seen:
            seen.add(url)
            job_urls.append(url)

    for url in job_urls:
        page = ctx.fetch(url)

        title_match = _OG_TITLE_RE.search(page) or _TITLE_RE.search(page)
        title = _clean_title(title_match.group(1)) if title_match else None

        location_match = _LOCATION_RE.search(page)
        location = _text(location_match.group(1)) if location_match else None

        article_match = _ARTICLE_RE.search(page)
        description = _text(article_match.group(0)) if article_match else None

        if not title or not location or not description:
            continue

        slug = url.rstrip("/").rsplit("/", 1)[-1]
        job = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": slug,
            "apply_url": url,
        }
        if "remote" in location.lower():
            job["workplace_type"] = "remote"

        yield job
