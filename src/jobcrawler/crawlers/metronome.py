
import html
import json
import re
from jobcrawler.runtime.contract import Job

SITE = "https://boards-api.greenhouse.io/v1/boards/metronome/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    # Greenhouse content on this board is entity-escaped more than once.
    decoded = value or ""
    for _ in range(3):
        unescaped = html.unescape(decoded)
        if unescaped == decoded:
            break
        decoded = unescaped

    decoded = re.sub(
        r"(?is)<(?:br|hr)\s*/?>|</(?:address|article|aside|blockquote|dd|div|"
        r"dl|dt|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\s*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", decoded)
    decoded = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", decoded)
    decoded = re.sub(r"(?s)<[^>]+>", "", decoded)
    decoded = html.unescape(decoded)

    lines = []
    for line in decoded.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _salary_fields(job):
    ranges = job.get("pay_input_ranges") or []
    currencies = {
        item.get("currency_type")
        for item in ranges
        if item.get("currency_type")
    }
    if not ranges or len(currencies) != 1:
        return {}

    minimums = [
        item["min_cents"] / 100
        for item in ranges
        if item.get("min_cents") is not None
    ]
    maximums = [
        item["max_cents"] / 100
        for item in ranges
        if item.get("max_cents") is not None
    ]

    result = {"salary_currency": next(iter(currencies))}
    if minimums:
        result["salary_min"] = min(minimums)
    if maximums:
        result["salary_max"] = max(maximums)

    periods = {
        item.get("interval")
        for item in ranges
        if item.get("interval")
    }
    if len(periods) == 1:
        result["salary_period"] = next(iter(periods))
    return result


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        description = _description(item.get("content"))
        if not item.get("absolute_url") or not item.get("title"):
            continue
        if not location or not description:
            continue

        fields = {
            "url": item["absolute_url"],
            "apply_url": item["absolute_url"],
            "title": item["title"],
            "company": "Metronome",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
        }

        offices = [
            office.get("name")
            for office in item.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in item.get("departments", [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        fields.update(_salary_fields(item))
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield Job(**fields)
