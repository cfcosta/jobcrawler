
import html
import json
import re

SITE = 'https://boards-api.greenhouse.io/v1/boards/netlify/jobs?content=true'
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|tr|section|article|ul|ol)\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for item in data.get("jobs", []):
        title = _clean(item.get("title"))
        url = _clean(item.get("absolute_url"))
        location = item.get("location") or {}
        location_name = _clean(location.get("name") if isinstance(location, dict) else location)
        description = _text_from_html(item.get("content"))

        job = {
            "url": url,
            "title": title,
            "company": "Netlify",
            "location": location_name,
            "description": description,
            "source": SITE,
        }

        external_id = item.get("id")
        if external_id is not None:
            job["external_id"] = str(external_id)

        posted_at = _clean(item.get("first_published"))
        if posted_at:
            job["posted_at"] = posted_at

        expires_at = _clean(item.get("application_deadline"))
        if expires_at:
            job["expires_at"] = expires_at

        apply_url = _clean(item.get("absolute_url"))
        if apply_url:
            job["apply_url"] = apply_url

        departments = item.get("departments")
        if departments:
            names = []
            for dep in departments:
                if isinstance(dep, dict):
                    name = _clean(dep.get("name"))
                    if name:
                        names.append(name)
            if names:
                job["department"] = ", ".join(names)

        yield job
