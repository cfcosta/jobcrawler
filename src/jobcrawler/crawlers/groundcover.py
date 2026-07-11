
import html
import json
import re

SITE = 'https://www.comeet.co/careers-api/2.0/company/88.008/positions?token=88833303BB81998222022202220044403330&details=true'
EXTRA_HOSTS = []


def _text_from_html(value):
    if not value:
        return None
    value = html.unescape(value)
    value = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", value)
    value = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h[1-6])\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def _location_name(location):
    if not isinstance(location, dict):
        return None
    name = location.get("name")
    if name:
        return str(name).strip() or None
    parts = [location.get("city"), location.get("state"), location.get("country")]
    text = ", ".join(str(p).strip() for p in parts if p)
    return text or None


def _description(position):
    sections = []
    for section in sorted(position.get("details") or [], key=lambda s: s.get("order") or 0):
        title = section.get("name")
        body = _text_from_html(section.get("value"))
        if body:
            if title:
                sections.append(str(title).strip() + "\n" + body)
            else:
                sections.append(body)
    return "\n\n".join(sections).strip() or None


def crawl(ctx):
    raw = ctx.fetch(SITE)
    if hasattr(raw, "text"):
        raw = raw.text
    positions = json.loads(raw)
    for position in positions:
        title = (position.get("name") or "").strip()
        description = _description(position)
        location = _location_name(position.get("location")) or "Remote"
        url = position.get("url_active_page") or position.get("url_comeet_hosted_page") or position.get("url_recruit_hosted_page") or position.get("position_url")
        if not (title and description and url):
            continue

        job = {
            "url": url,
            "title": title,
            "company": position.get("company_name") or "groundcover",
            "location": location,
            "description": description,
            "source": SITE,
        }

        if location:
            job["locations"] = [location]
        if position.get("department"):
            job["department"] = position.get("department")
        if position.get("employment_type"):
            job["employment_type"] = position.get("employment_type")
        if position.get("workplace_type"):
            job["workplace_type"] = position.get("workplace_type")
        if position.get("time_updated"):
            job["posted_at"] = position.get("time_updated")
        if position.get("uid"):
            job["external_id"] = position.get("uid")
        if url:
            job["apply_url"] = url

        yield job
