
import html
import json
import re



SITE = "https://rss.hirebridge.com/7866.json"
EXTRA_HOSTS = []


def _html_to_text(value):
    if not value:
        return None

    value = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"</?(?:address|article|aside|blockquote|br|div|footer|h[1-6]|header|hr|li|main|ol|p|pre|section|table|td|th|tr|ul)\b[^>]*>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)

    lines = []
    for line in value.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) or None


def _put(values, name, value):
    if value is not None and value != "":
        values[name] = value



_MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def _iso_date(value):
    if not value:
        return None
    match = re.match(
        r"^[A-Za-z]{3},\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\b",
        value,
    )
    if not match:
        return None
    day, month, year = match.groups()
    month_number = _MONTHS.get(month)
    if not month_number:
        return None
    return year + "-" + month_number + "-" + day.zfill(2)


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE))
    jobs = payload.get("source", {}).get("job", [])
    if isinstance(jobs, dict):
        jobs = [jobs]

    for item in jobs:
        title = item.get("title")
        url = item.get("url")
        location = item.get("location")
        description = _html_to_text(item.get("description"))

        if not title or not url or not location or not description:
            continue

        values = {
            "url": url,
            "title": title,
            "company": item.get("company") or "Applause",
            "location": location,
            "description": description,
            "source": SITE,
        }

        _put(values, "external_id", item.get("referencenumber"))
        _put(values, "apply_url", item.get("ApplicationURL"))
        _put(values, "department", item.get("department"))
        _put(values, "employment_type", item.get("jobtype"))
        _put(values, "posted_at", _iso_date(item.get("date")))
        _put(values, "salary", item.get("salary"))

        yield values
