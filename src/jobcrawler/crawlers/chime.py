
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/chime/jobs?content=true'
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _html_to_text(value):
    if not value:
        return None

    # Greenhouse returns full posting HTML as entity-escaped text.
    text = html.unescape(str(value))

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|ul|ol)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n(3,)", "\n\n", text)
    return text.strip() or None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = _clean(item.get("name"))
            if name:
                names.append(name)
    return names


def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)

    for job in data.get("jobs", []):
        title = _clean(job.get("title"))
        url = _clean(job.get("absolute_url"))
        location = _clean((job.get("location") or {}).get("name"))
        description = _html_to_text(job.get("content"))

        if not (title and url and location and description):
            continue

        departments = _names(job.get("departments"))
        offices = _names(job.get("offices"))

        item = {
            "url": url,
            "title": title,
            "company": _clean(job.get("company_name")) or "Chime",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": _clean(job.get("id")),
            "posted_at": _clean(job.get("first_published")),
            "expires_at": _clean(job.get("application_deadline")),
            "apply_url": url,
        }

        if departments:
            item["department"] = ", ".join(departments)
        elif offices:
            item["department"] = ", ".join(offices)

        yield {k: v for k, v in item.items() if v is not None}
