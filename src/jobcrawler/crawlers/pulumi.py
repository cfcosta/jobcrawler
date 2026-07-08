
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/pulumicorporation/jobs?content=true"
EXTRA_HOSTS = []

try:
    Job
except NameError:
    Job = dict

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


def _text_from_greenhouse_content(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = _TAG_RE.sub(" ", value)
    value = html.unescape(value)
    value = _SPACE_RE.sub(" ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def crawl(ctx):
    response = ctx.fetch(SITE)
    if hasattr(response, "text"):
        raw = response.text
    else:
        raw = response
    data = json.loads(raw)
    for item in data.get("jobs", []):
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        location = _clean((item.get("location") or {}).get("name"))
        description = _text_from_greenhouse_content(item.get("content"))
        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Pulumi",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield Job(**job) if callable(Job) and Job is not dict else job
