
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/remotecom/jobs?content=true"


def _strip_html(value):
    if not value:
        return None

    # Greenhouse returns entity-escaped HTML; unescape until tags/entities are readable.
    text = html.unescape(value)
    text = html.unescape(text)

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|section|article|h[1-6]|ul|ol|li|tr|table|blockquote)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return ", ".join(names) if names else None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for job in payload.get("jobs", []):
        title = _clean(job.get("title"))
        url = _clean(job.get("absolute_url"))
        location = _clean((job.get("location") or {}).get("name"))
        description = _strip_html(job.get("content"))
        company = _clean(job.get("company_name")) or "Remote"

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _clean(job.get("id")) or _clean(job.get("requisition_id")) or _clean(job.get("internal_job_id"))
        if external_id is not None:
            item["external_id"] = str(external_id)

        department = _first_name(job.get("departments"))
        if department:
            item["department"] = department

        posted_at = _clean(job.get("first_published"))
        if posted_at:
            item["posted_at"] = posted_at

        expires_at = _clean(job.get("application_deadline"))
        if expires_at:
            item["expires_at"] = expires_at

        yield item
