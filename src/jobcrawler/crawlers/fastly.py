
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/fastly/jobs?content=true"
EXTRA_HOSTS = []


def _clean(value):
    return value if value not in (None, "") else None


def _html_to_text(value):
    if not value:
        return None

    text = html.unescape(value)

    # Preserve useful structure before dropping HTML tags.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|h1|h2|h3|h4|li|ul|ol|tr|table|section|article)\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "\n- ", text)
    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text or None


def _metadata_value(job, name):
    for item in job.get("metadata") or []:
        if item.get("name") == name and item.get("value") not in (None, ""):
            return item.get("value")
    return None


def _department(job):
    departments = job.get("departments") or []
    names = [d.get("name") for d in departments if d.get("name")]
    if names:
        return ", ".join(names)
    return _metadata_value(job, "Workday Department")


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))

    for job in data.get("jobs") or []:
        title = _clean(job.get("title"))
        url = _clean(job.get("absolute_url"))
        location = _clean((job.get("location") or {}).get("name"))
        description = _html_to_text(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": _clean(job.get("company_name")) or "Fastly",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(job.get("id")) if job.get("id") is not None else None,
            "apply_url": url,
            "posted_at": _clean(job.get("first_published")),
            "expires_at": _clean(job.get("application_deadline")),
            "department": _department(job),
            "workplace_type": _metadata_value(job, "Work Location Type"),
        }

        requisition_id = _clean(job.get("requisition_id"))
        if requisition_id:
            item["external_id"] = requisition_id

        yield {k: v for k, v in item.items() if v not in (None, "")}
