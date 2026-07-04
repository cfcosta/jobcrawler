import json, html, re

SITE = "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true"
API_URL = "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true"
COMPANY = "Figma"


def _strip_tags(html_text):
    if not html_text:
        return ""
    unescaped = html.unescape(html_text)
    text = re.sub(r"<[^>]+>", " ", unescaped)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def crawl(ctx):
    resp = ctx.fetch(API_URL)
    data = json.loads(resp)
    jobs = data.get("jobs", [])
    seen = set()
    for j in jobs:
        jid = j.get("id")
        if jid is None or jid in seen:
            continue
        seen.add(jid)
        url = j.get("absolute_url") or ""
        title = j.get("title") or ""
        loc_obj = j.get("location") or {}
        location = loc_obj.get("name") or ""
        company = j.get("company_name") or COMPANY
        description = _strip_tags(j.get("content") or "")
        departments = j.get("departments") or []
        department = (
            "; ".join(d.get("name", "") for d in departments if d.get("name")) or None
        )
        posted_at = j.get("first_published") or None
        expires_at = j.get("application_deadline") or None
        yield {
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "department": department,
            "posted_at": posted_at,
            "expires_at": expires_at,
            "external_id": str(jid),
            "apply_url": url,
            "source": SITE,
        }
