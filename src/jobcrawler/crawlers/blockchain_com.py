
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/blockchain/jobs?content=true"


def _text_from_response(resp):
    if hasattr(resp, "text"):
        return resp.text
    if isinstance(resp, bytes):
        return resp.decode("utf-8", "replace")
    return str(resp)


def _clean_html(value):
    if not value:
        return None
    # Greenhouse API escapes the full HTML body; decode entities before tag stripping.
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not items:
        return None
    names = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return ", ".join(names) if names else None


def crawl(ctx):
    raw = _text_from_response(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        description = _clean_html(job.get("content"))
        location = None
        if isinstance(job.get("location"), dict):
            location = job["location"].get("name")
        if not (title and url and description and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": job.get("company_name") or "Blockchain.com",
            "location": location,
            "description": description,
            "source": SITE,
        }

        department = _first_name(job.get("departments"))
        if department:
            out["department"] = department
        if job.get("first_published"):
            out["posted_at"] = job["first_published"]
        if job.get("id") is not None:
            out["external_id"] = str(job["id"])
        if job.get("absolute_url"):
            out["apply_url"] = job["absolute_url"]
        if job.get("application_deadline"):
            out["expires_at"] = job["application_deadline"]

        yield out
