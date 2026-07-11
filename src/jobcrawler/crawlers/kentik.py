"""Crawler for Kentik's Greenhouse job board."""

import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/kentik/jobs?content=true"
COMPANY = "Kentik"


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not items:
        return None
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            return item["name"]
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        location = None
        if isinstance(job.get("location"), dict):
            location = job["location"].get("name")

        out = {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": job.get("company_name") or COMPANY,
            "location": location,
            "description": _clean_html(job.get("content")),
            "source": SITE,
        }

        department = _first_name(job.get("departments"))
        if department:
            out["department"] = department

        external_id = job.get("id")
        if external_id is not None:
            out["external_id"] = str(external_id)

        if job.get("absolute_url"):
            out["apply_url"] = job["absolute_url"]

        posted_at = job.get("first_published")
        if posted_at:
            out["posted_at"] = posted_at

        yield {k: v for k, v in out.items() if v not in (None, "")}
