
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/castaigroupinc/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article)\s*>", "\n", text)
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_name(items):
    if not items:
        return None
    for item in items:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            return name
    return None


def _names(items):
    names = []
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return names or None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    for item in payload.get("jobs", []):
        title = item.get("title")
        url = item.get("absolute_url")
        location = (item.get("location") or {}).get("name")
        description = _clean_html(item.get("content"))

        if not (title and url and location and description):
            continue

        job = {
            "url": url,
            "title": title,
            "company": item.get("company_name") or "Cast AI",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item.get("id")),
            "apply_url": url,
        }

        department = _first_name(item.get("departments"))
        if department:
            job["department"] = department

        locations = _names(item.get("offices"))
        if locations:
            job["locations"] = locations

        if item.get("first_published"):
            job["posted_at"] = item["first_published"]
        if item.get("application_deadline"):
            job["expires_at"] = item["application_deadline"]

        yield job
