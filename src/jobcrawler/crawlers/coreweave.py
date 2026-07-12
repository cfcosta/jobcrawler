import html
import json
import re


SITE = 'https://boards-api.greenhouse.io/v1/boards/coreweave/jobs?content=true'
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|</?\s*(?:address|article|aside|blockquote|"
        r"dd|div|dl|dt|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|"
        r"pre|section|table|td|th|tr|ul)(?:\s[^>]*)?>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<script(?:\s[^>]*)?>.*?</script>", "", value)
    value = re.sub(r"(?is)<style(?:\s[^>]*)?>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _names(values):
    return [
        value["name"]
        for value in (values or [])
        if isinstance(value, dict) and value.get("name")
    ]


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name")
        offices = _names(item.get("offices"))
        departments = _names(item.get("departments"))

        fields = {
            "url": item.get("absolute_url"),
            "source": SITE,
            "title": item.get("title"),
            "company": "CoreWeave",
            "location": location,
            "description": _plain_text(item.get("content")),
            "external_id": str(item["id"]) if item.get("id") is not None else None,
            "posted_at": item.get("first_published"),
            "expires_at": item.get("application_deadline"),
            "department": ", ".join(departments) if departments else None,
        }
        if offices:
            fields["locations"] = offices

        ranges = item.get("pay_input_ranges") or []
        currencies = {
            value.get("currency_type")
            for value in ranges
            if value.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                value["min_cents"] / 100
                for value in ranges
                if value.get("min_cents") is not None
            ]
            maximums = [
                value["max_cents"] / 100
                for value in ranges
                if value.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))

        yield {key: value for key, value in fields.items() if value is not None}
