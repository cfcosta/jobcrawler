
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/gleanwork/jobs?content=true"

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None

def _first_name(items):
    if not items:
        return None
    names = [x.get("name") for x in items if isinstance(x, dict) and x.get("name")]
    return ", ".join(names) if names else None

def _clean_dict(fields):
    return {k: v for k, v in fields.items() if v is not None and v != ""}

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _clean_html(item.get("content"))
        if not (title and url and location and description):
            continue

        yield _clean_dict({
            "source": SITE,
            "url": url,
            "title": title,
            "company": item.get("company_name") or "Glean",
            "location": location,
            "description": description,
            "department": _first_name(item.get("departments")),
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "external_id": str(item.get("id")) if item.get("id") is not None else None,
            "apply_url": url,
        })
