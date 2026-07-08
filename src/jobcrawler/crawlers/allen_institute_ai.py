
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/thealleninstitute/jobs?content=true"
EXTRA_HOSTS = []


_BLOCK_TAG_RE = re.compile(r"</?(?:address|article|aside|blockquote|br|dd|div|dl|dt|fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_SPACE_RE = re.compile(r" *\n *")
_MANY_NL_RE = re.compile(r"\n{3,}")


def _html_to_text(value):
    if not value:
        return None
    text = html.unescape(value)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _NL_SPACE_RE.sub("\n", text)
    text = _MANY_NL_RE.sub("\n\n", text).strip()
    return text or None


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _department(job):
    departments = job.get("departments") or []
    names = []
    for department in departments:
        if isinstance(department, dict):
            name = _clean(department.get("name"))
            if name:
                names.append(name)
    return ", ".join(names) if names else None


def _job_obj(**fields):
    return {key: value for key, value in fields.items() if value is not None and value != ""}


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs") or []:
        if not isinstance(item, dict):
            continue

        location = item.get("location") or {}
        if isinstance(location, dict):
            location_name = _clean(location.get("name"))
        else:
            location_name = _clean(location)

        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        description = _html_to_text(item.get("content"))
        if not (title and url and location_name and description):
            continue

        external_id = _clean(item.get("id")) or _clean(item.get("internal_job_id"))

        yield _job_obj(
            url=url,
            title=title,
            company=_clean(item.get("company_name")) or "The Allen Institute for Artificial Intelligence",
            location=location_name,
            description=description,
            source=SITE,
            department=_department(item),
            posted_at=_clean(item.get("first_published")),
            expires_at=_clean(item.get("application_deadline")),
            external_id=external_id,
            apply_url=url,
        )
