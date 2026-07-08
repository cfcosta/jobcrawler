
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/falconx/jobs?content=true"
EXTRA_HOSTS = []

COMPANY = "FalconX"

_BR_RE = re.compile(r"(?i)<\s*br\s*/?\s*>")
_BLOCK_RE = re.compile(r"(?i)</\s*(?:p|div|li|h[1-6]|ul|ol|blockquote|section|tr)\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_SEP_RE = re.compile(r"[  \xa0]")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _SEP_RE.sub(" ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _department(job):
    names = [d.get("name") for d in job.get("departments") or [] if d.get("name")]
    return "; ".join(names) if names else None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        location = job.get("location") or {}
        item = {
            "url": job.get("absolute_url"),
            "title": job.get("title"),
            "company": job.get("company_name") or COMPANY,
            "location": location.get("name") if isinstance(location, dict) else None,
            "description": _clean_html(job.get("content")),
            "source": "falconx",
            "external_id": str(job["id"]) if job.get("id") else None,
            "apply_url": job.get("absolute_url"),
            "posted_at": job.get("first_published"),
            "expires_at": job.get("application_deadline"),
            "department": _department(job),
        }
        yield {k: v for k, v in item.items() if v not in (None, "")}
