
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/fireblocks/jobs?content=true"


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                names.append(str(name).strip())
    return [n for n in names if n]


def _set_if(job, key, value):
    if value is not None and value != "":
        job[key] = value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for raw in data.get("jobs", []):
        url = raw.get("absolute_url")
        title = raw.get("title")
        location = (raw.get("location") or {}).get("name")
        description = _clean_html(raw.get("content"))

        if not (url and title and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": raw.get("company_name") or "Fireblocks",
            "location": location,
            "description": description,
            "source": SITE,
        }

        departments = _names(raw.get("departments"))
        if departments:
            job["department"] = ", ".join(departments)

        _set_if(job, "posted_at", raw.get("first_published"))
        _set_if(job, "expires_at", raw.get("application_deadline"))
        _set_if(job, "external_id", str(raw.get("id")) if raw.get("id") is not None else None)
        _set_if(job, "apply_url", url)

        yield job
