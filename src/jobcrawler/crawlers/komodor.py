
import html
import json
import re

SITE = "https://www.comeet.co/careers-api/2.0/company/96.005/positions?token=695277ED2A020E9277E69534A81A54277E&details=true"
EXTRA_HOSTS = []


def _text(value):
    value = value or ""
    value = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        "",
        value,
        flags=re.I | re.S,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|dl|dt|dd|fieldset|"
        r"figcaption|figure|footer|form|h[1-6]|header|hr|li|main|nav|ol|p|"
        r"pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _description(details):
    sections = []
    for detail in details or []:
        body = _text(detail.get("value"))
        if not body:
            continue
        name = detail.get("name")
        sections.append("%s\n%s" % (name, body) if name else body)
    return "\n\n".join(sections)


def crawl(ctx):
    positions = json.loads(ctx.fetch(SITE))

    for position in positions:
        location = position.get("location") or {}
        kwargs = {
            "url": position["position_url"],
            "source": SITE,
            "title": position["name"],
            "company": "Komodor",
            "location": location["name"],
            "description": _description(position.get("details")),
            "external_id": position.get("uid"),
            "apply_url": position.get("url_active_page"),
            "department": position.get("department"),
            "employment_type": position.get("employment_type"),
            "workplace_type": position.get("workplace_type"),
        }
        yield {
            key: value
            for key, value in kwargs.items()
            if value is not None and value != ""
        }
