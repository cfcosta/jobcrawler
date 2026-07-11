"""Crawler for Automattic's careers board.

Automattic (WordPress.com, WooCommerce, Jetpack, Tumblr, Beeper) is a fully
distributed company. Its careers page is Greenhouse-backed, but the public
Greenhouse board token does not resolve; instead the open roles are injected
into the page as an inline ``const ghJobsData = [...]`` JSON array (rendered
client-side by the WordPress ``wwu-positions`` block). A plain static GET of
the jobs page returns the full dataset -- id, title, HTML content, category,
team and job URL -- so we parse that inline blob directly.
"""

import json, html, re

SITE = "https://automattic.com/work-with-us/jobs/"
COMPANY = "Automattic"
EXTRA_HOSTS = []

_MARKER = "const ghJobsData = "
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(raw_text: str) -> str:
    """Unescape entity-encoded HTML, strip tags, normalize whitespace.

    The ``content`` field is doubly encoded: HTML tags arrive as entities
    (``&lt;p&gt;``) wrapping text that itself carries encoded entities, so we
    unescape, strip the now-real tags, then unescape once more to resolve any
    remaining layer before collapsing whitespace.
    """
    if not raw_text:
        return ""
    text = html.unescape(raw_text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def crawl(ctx):
    """Yield one Job dict per open Automattic role from the inline ghJobsData blob."""
    raw = ctx.fetch(SITE)

    idx = raw.find(_MARKER)
    if idx == -1:
        return
    idx += len(_MARKER)
    data, _ = json.JSONDecoder().raw_decode(raw[idx:])

    for job in data:
        # The blob mixes open roles ("job") with a general-interest form
        # ("interest"); only the former are real openings.
        if job.get("type") != "job":
            continue

        title = (job.get("title") or "").strip()
        url = job.get("href")
        description = _strip_tags(job.get("content", ""))
        if not (title and url and description):
            continue

        meta = job.get("metadata") or {}
        categories = [c for c in (meta.get("Category") or []) if c]

        # Automattic is a fully distributed / remote-first employer and exposes
        # no per-role location field, so remote is the honest default.
        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": "Remote",
            "description": description,
            "source": SITE,
            "workplace_type": "Remote",
        }

        job_id = job.get("id")
        if job_id:
            out["external_id"] = str(job_id)

        out["apply_url"] = url

        if categories:
            out["department"] = "; ".join(categories)

        yield out
