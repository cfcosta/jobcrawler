import json
import re
import html as _htmllib

SITE = "https://www.comeet.com/jobs/rapyd/73.00E"
EXTRA_HOSTS = []

_COMPANY = "Rapyd"

# The Comeet careers-api positions endpoint 404s without a valid API token
# (the token embedded in the page is not accepted by the JSON API). But the
# public hosted page server-renders the full positions array as a JavaScript
# variable `COMPANY_POSITIONS_DATA = [ ... ];`, including per-position
# description HTML. We parse that blob directly.
_POSITIONS_RE = re.compile(r"COMPANY_POSITIONS_DATA\s*=\s*(\[)")

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_BREAK_RE = re.compile(r"(?i)</(?:p|div|li|tr|h[1-6])>|<br\s*/?>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NL_RE = re.compile(r"\n\s*\n\s*\n+")


def _strip_html(value):
    if not value:
        return ""
    text = _BLOCK_BREAK_RE.sub("\n", value)
    text = _TAG_RE.sub("", text)
    text = _htmllib.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _extract_positions(page):
    """Pull the JSON array assigned to COMPANY_POSITIONS_DATA out of the page."""
    match = _POSITIONS_RE.search(page)
    if not match:
        return []
    start = match.start(1)
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(page)):
        ch = page[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                blob = page[start : i + 1]
                try:
                    data = json.loads(blob)
                except ValueError:
                    return []
                return data if isinstance(data, list) else []
    return []


def _description(position):
    details = ((position.get("custom_fields") or {}).get("details")) or []
    parts = []
    for detail in details:
        if not isinstance(detail, dict):
            continue
        text = _strip_html(detail.get("value"))
        if not text:
            continue
        name = (detail.get("name") or "").strip()
        if name and name.lower() not in ("description", "job description"):
            parts.append("%s\n%s" % (name, text))
        else:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _location_string(location):
    if not location:
        return ""
    if isinstance(location, str):
        return location.strip()
    name = (location.get("name") or "").strip()
    if name:
        return name
    pieces = [location.get("city"), location.get("state"), location.get("country")]
    joined = ", ".join(p.strip() for p in pieces if p and p.strip())
    if joined:
        return joined
    if location.get("is_remote"):
        return "Remote"
    return ""


def crawl(ctx):
    page = ctx.fetch(SITE)
    positions = _extract_positions(page)
    ctx.log("rapyd: parsed %d positions" % len(positions))

    for position in positions:
        if not isinstance(position, dict):
            continue

        title = position.get("name")
        url = position.get("url_comeet_hosted_page") or position.get("url_active_page")
        description = _description(position)
        location_obj = position.get("location") or {}
        location = _location_string(location_obj)

        if not (title and url and description):
            continue
        if not location:
            location = "Remote" if location_obj.get("is_remote") else "Unspecified"

        out = {
            "url": url,
            "title": title,
            "company": _COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = position.get("uid")
        if external_id:
            out["external_id"] = external_id

        apply_url = position.get("url_active_page") or position.get(
            "url_recruit_hosted_page"
        )
        if apply_url and apply_url != url:
            out["apply_url"] = apply_url

        department = position.get("department")
        if department:
            out["department"] = department

        employment_type = position.get("employment_type")
        if employment_type:
            out["employment_type"] = employment_type

        workplace_type = position.get("workplace_type")
        if not workplace_type and location_obj.get("is_remote"):
            workplace_type = "Remote"
        if workplace_type:
            out["workplace_type"] = workplace_type

        posted_at = position.get("time_updated")
        if posted_at:
            out["posted_at"] = posted_at

        if location:
            out["locations"] = [location]

        yield out
