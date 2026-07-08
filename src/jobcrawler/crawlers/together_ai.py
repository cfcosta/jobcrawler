
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/togetherai/jobs?content=true"
EXTRA_HOSTS = []


_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|table|section|article)[^>]*>", re.I)


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _first_name(items):
    names = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]).strip())
    return ", ".join(n for n in names if n) or None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = (job.get("title") or "").strip()
        url = (job.get("absolute_url") or "").strip()
        description = _clean_html(job.get("content"))
        location = None
        if isinstance(job.get("location"), dict):
            location = (job["location"].get("name") or "").strip() or None

        if not (title and url and description and location):
            continue

        item = {
            "url": url,
            "title": title,
            "company": (job.get("company_name") or "Together AI").strip(),
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")),
        }

        posted_at = job.get("first_published")
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = job.get("application_deadline")
        if expires_at:
            item["expires_at"] = expires_at

        department = _first_name(job.get("departments"))
        if department:
            item["department"] = department

        # Greenhouse's canonical posting page is also the application URL for this board.
        item["apply_url"] = url

        yield item
