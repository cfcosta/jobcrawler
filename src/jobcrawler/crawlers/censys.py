
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/censys/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = value or ""
    # Greenhouse content is HTML-entity escaped and can contain nested entities.
    for _ in range(2):
        text = html.unescape(text)

    text = re.sub(
        r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1\s*>",
        "",
        text,
    )
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)<\s*li\b[^>]*>", "\n- ", text)
    text = re.sub(
        r"(?i)</?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|"
        r"figcaption|figure|footer|h[1-6]|header|hr|main|nav|ol|p|pre|"
        r"section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _names(items):
    return [
        item["name"]
        for item in (items or [])
        if isinstance(item, dict) and item.get("name")
    ]


def crawl(ctx):
    raw = ctx.fetch(SITE + "&pay_transparency=true")
    data = json.loads(raw)
    seen = set()

    for posting in data.get("jobs", []):
        job_id = str(posting["id"])
        if job_id in seen:
            continue
        seen.add(job_id)

        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))
        url = posting.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": "Censys",
            "location": location,
            "description": description,
            "source": "greenhouse",
            "external_id": job_id,
        }

        offices = _names(posting.get("offices"))
        if offices:
            fields["locations"] = offices

        departments = _names(posting.get("departments"))
        if departments:
            fields["department"] = ", ".join(departments)

        if posting.get("first_published"):
            fields["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if isinstance(item, dict) and item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                item["min_cents"] / 100
                for item in ranges
                if isinstance(item, dict) and item.get("min_cents") is not None
            ]
            maximums = [
                item["max_cents"] / 100
                for item in ranges
                if isinstance(item, dict) and item.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield fields
