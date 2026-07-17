
import json
import re
import html
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/gusto/jobs?content=true"
EXTRA_HOSTS = []

_TAG_BREAK_RE = re.compile(r"(?i)</?(?:p|div|br|li|ul|ol|h[1-6]|section|tr|table|blockquote|hr)\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")

def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = _TAG_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NL_RE.sub("\n\n", text).strip()
    return text or None

def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
        elif value:
            return value
    return None

def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict):
            name = _first_nonempty(item.get("name"))
            if name:
                names.append(name)
    return names

def _make_job(**fields):
    fields = {k: v for k, v in fields.items() if v not in (None, "", [], {})}
    try:
        return Job(**fields)
    except NameError:
        return fields

def crawl(ctx):
    raw = ctx.fetch(SITE)
    data = json.loads(raw)
    for item in data.get("jobs", []):
        location = item.get("location") or {}
        department_names = _names(item.get("departments"))
        description = _clean_html(item.get("content"))
        yield _make_job(
            source=SITE,
            url=_first_nonempty(item.get("absolute_url")),
            title=_first_nonempty(item.get("title")),
            company=_first_nonempty(item.get("company_name"), "Gusto"),
            location=_first_nonempty(location.get("name") if isinstance(location, dict) else location, "Remote"),
            description=description,
            department=", ".join(department_names) if department_names else None,
            posted_at=_first_nonempty(item.get("first_published")),
            expires_at=_first_nonempty(item.get("application_deadline")),
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            apply_url=_first_nonempty(item.get("absolute_url")),
        )
