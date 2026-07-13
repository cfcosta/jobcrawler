
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/ngrokinc/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    text = html.unescape(value or "")
    text = re.sub(
        r"(?is)<(?:br\s*/?|/p|/div|/li|/h[1-6]|/tr|/section|/article|/ul|/ol)\s*>",
        "\n",
        text,
    )
    text = re.sub(r"(?is)<li(?:\s[^>]*)?>", "\n", text)
    text = re.sub(r"(?is)<script(?:\s[^>]*)?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style(?:\s[^>]*)?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)

    for posting in data.get("jobs", []):
        fields = {
            "url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": posting.get("company_name") or "ngrok Inc.",
            "location": (posting.get("location") or {}).get("name"),
            "description": _description(posting.get("content")),
            "source": SITE,
            "external_id": str(posting["id"]),
        }

        offices = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]
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

        yield fields
