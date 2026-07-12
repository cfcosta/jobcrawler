import html
import json
import re

SITE = 'https://www.comeet.co/careers-api/2.0/company/26.004/positions?token=62424D80374424D824D8126C126C1EB41890&details=true'
EXTRA_HOSTS = []


def _strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"<script\\b[^>]*>.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(
        r"<\\s*(?:br|hr)\\s*/?\\s*>|</?\\s*(?:p|div|li|ul|ol|h[1-6]|"
        r"section|article|blockquote|tr|table|pre|dl|dt|dd)\\b[^>]*>",
        "\\n",
        value,
        flags=re.I,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\\xa0", " ")

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\\n".join(lines)


def _payload(text):
    text = text.strip()
    if text.startswith("<"):
        match = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.I | re.S)
        if match:
            text = html.unescape(match.group(1))
    return json.loads(text)


def _description(details):
    sections = []
    for section in details or []:
        body = _strip_html(section.get("value"))
        if not body:
            continue
        heading = (section.get("name") or "").strip()
        sections.append(heading + "\\n" + body if heading else body)
    return "\\n\\n".join(sections)


def _salary_text(description):
    match = re.search(r"Base Salary Range:\\s*[^\\n]+", description, re.I)
    return match.group(0).strip() if match else None


def crawl(ctx):
    records = _payload(ctx.fetch(SITE))

    for record in records:
        location = record.get("location")

        # Comeet exposes this location-less record as an evergreen talent pool.
        if not location or record.get("name") == "Join the team":
            continue

        description = _description(record.get("details"))
        if not description:
            continue

        job = {
            "url": record.get("position_url") or SITE,
            "title": record.get("name"),
            "company": "Firebolt",
            "location": location.get("name"),
            "description": description,
            "source": SITE,
            "department": record.get("department"),
            "employment_type": record.get("employment_type"),
            "workplace_type": record.get("workplace_type"),
            "external_id": record.get("uid"),
            "apply_url": (
                record.get("url_comeet_hosted_page")
                or record.get("url_recruit_hosted_page")
                or record.get("url_active_page")
            ),
            "updated_at": record.get("time_updated"),
            "salary": _salary_text(description),
        }

        yield {key: value for key, value in job.items() if value is not None and value != ""}
