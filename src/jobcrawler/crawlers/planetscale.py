
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/planetscale/jobs?content=true"
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|section|tr)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    payload = ctx.fetch(SITE)
    data = json.loads(payload)
    for job in data.get("jobs", []):
        title = job.get("title")
        url = job.get("absolute_url")
        loc = job.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else loc
        description = _text_from_html(job.get("content"))

        if not (title and url and location and description):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "PlanetScale",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id") or job.get("requisition_id") or job.get("internal_job_id")
        if external_id is not None:
            out["external_id"] = str(external_id)

        apply_url = job.get("absolute_url")
        if apply_url:
            out["apply_url"] = apply_url

        yield out
