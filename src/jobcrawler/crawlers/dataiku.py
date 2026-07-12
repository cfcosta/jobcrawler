
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/dataiku/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(
        r"(?i)<\s*(?:br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/table)\s*/?\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in data.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        url = posting.get("absolute_url")

        fields = {
            "url": url,
            "apply_url": url,
            "title": posting.get("title"),
            "company": posting.get("company_name") or "Dataiku",
            "location": location,
            "description": _plain_text(posting.get("content")),
            "source": SITE,
            "external_id": (
                str(posting["id"]) if posting.get("id") is not None else None
            ),
            "posted_at": posting.get("first_published"),
            "expires_at": posting.get("application_deadline"),
        }

        offices = [
            office.get("name")
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department.get("name")
            for department in posting.get("departments") or []
            if department.get("name")
        ]
        if departments:
            fields["department"] = departments[0]

        ranges = posting.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in ranges
            if pay_range.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in ranges
                if pay_range.get("max_cents") is not None
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
