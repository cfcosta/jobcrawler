
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/grafanalabs/jobs?content=true"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n+")


def _clean_html(value):
    if not value:
        return None
    # Greenhouse board API returns HTML content with entities escaped.
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = _BLANK_RE.sub("\n\n", text).strip()
    return text or None


def _value(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _metadata_value(job, names):
    metadata = job.get("metadata") or []
    if not isinstance(metadata, list):
        return None
    wanted = {name.lower() for name in names}
    for item in metadata:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name not in wanted:
            continue
        val = item.get("value")
        if isinstance(val, list):
            parts = []
            for entry in val:
                if isinstance(entry, dict):
                    entry = entry.get("name") or entry.get("value")
                entry = _value(entry)
                if entry:
                    parts.append(str(entry))
            return ", ".join(parts) or None
        if isinstance(val, dict):
            val = val.get("name") or val.get("value")
        return _value(val)
    return None


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs", []):
        title = _value(job.get("title"))
        url = _value(job.get("absolute_url"))
        location_obj = job.get("location") or {}
        location = _value(location_obj.get("name") if isinstance(location_obj, dict) else location_obj)
        description = _clean_html(job.get("content"))

        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Grafana Labs",
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = _value(job.get("requisition_id")) or _value(job.get("id"))
        if external_id is not None:
            item["external_id"] = str(external_id)

        department = _metadata_value(job, ["Department", "Team"])
        if department:
            item["department"] = department

        employment_type = _metadata_value(job, ["Employment Type", "Commitment"])
        if employment_type:
            item["employment_type"] = employment_type

        workplace_type = _metadata_value(job, ["Workplace Type", "Workplace"])
        if workplace_type:
            item["workplace_type"] = workplace_type

        yield item
