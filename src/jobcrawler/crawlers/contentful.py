import html
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SITE = "https://boards-api.greenhouse.io/v1/boards/contentful/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?i)<(?:br|hr)\s*/?>|</?(?:address|article|aside|blockquote|div|"
        r"dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|"
        r"p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", text, flags=re.I)
    text = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", text, flags=re.I)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _discovery_url():
    parts = urlsplit(SITE)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["content"] = "true"
    query["pay_transparency"] = "true"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
    )


def crawl(ctx):
    raw = ctx.fetch(_discovery_url())
    payload = json.loads(raw)

    for posting in payload.get("jobs", []):
        title = posting.get("title")
        url = posting.get("absolute_url")
        location = (posting.get("location") or {}).get("name")
        description = _plain_text(posting.get("content"))

        if not title or not url or not location or not description:
            continue

        fields = {
            "url": url,
            "title": title,
            "company": posting.get("company_name") or "Contentful",
            "location": location,
            "description": description,
            "source": raw,
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }

        locations = [
            office.get("name")
            for office in (posting.get("offices") or [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        departments = [
            department.get("name")
            for department in (posting.get("departments") or [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            item.get("currency_type")
            for item in ranges
            if item.get("currency_type")
        }
        if ranges and len(currencies) == 1:
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
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield {
            key: value
            for key, value in fields.items()
            if value is not None
        }
