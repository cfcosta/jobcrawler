
import json
import re
import html

SITE = "https://boards-api.greenhouse.io/v1/boards/marqeta/jobs?content=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _strip_html(value):
    text = html.unescape(value or "")

    # Preserve rough block/list boundaries before removing tags.
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*/?\s*(p|div|section|article|h[1-6]|ul|ol|tr|table)\b[^>]*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<\s*/\s*li\s*>", "\n", text)

    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def crawl(ctx):
    payload = ctx.fetch(SITE)
    data = json.loads(payload)

    for item in data.get("jobs") or []:
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        description = _strip_html(item.get("content") or "")

        location_data = item.get("location") or {}
        if isinstance(location_data, dict):
            location = _clean(location_data.get("name"))
        else:
            location = _clean(location_data)

        if not (title and url and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": "Marqeta",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is None:
            external_id = item.get("internal_job_id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        yield job
