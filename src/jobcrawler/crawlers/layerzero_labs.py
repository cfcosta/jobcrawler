
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/layerzerolabs/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(br|/p|/div|/li|/ul|/ol|/h[1-6]|/tr|/section)\s*/?\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<\s*li(?:\s[^>]*)?>", "- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\r", "")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))
    seen = set()

    for posting in payload.get("jobs", []):
        external_id = str(posting["id"])
        if external_id in seen:
            continue
        seen.add(external_id)

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"].strip(),
            "company": "LayerZero Labs",
            "source": SITE,
            "location": (posting.get("location") or {}).get("name"),
            "description": _plain_text(posting.get("content")),
            "external_id": external_id,
            "apply_url": posting.get("absolute_url"),
            "posted_at": posting.get("first_published"),
        }

        departments = [
            item["name"]
            for item in posting.get("departments", [])
            if item.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        locations = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if locations:
            fields["locations"] = locations

        if posting.get("application_deadline"):
            fields["expires_at"] = posting["application_deadline"]

        ranges = posting.get("pay_input_ranges") or []
        currencies = {item.get("currency_type") for item in ranges}
        minimums = [item.get("min_cents") for item in ranges]
        maximums = [item.get("max_cents") for item in ranges]
        if (
            ranges
            and len(currencies) == 1
            and None not in currencies
            and all(value is not None for value in minimums + maximums)
        ):
            fields["salary_min"] = min(minimums) / 100
            fields["salary_max"] = max(maximums) / 100
            fields["salary_currency"] = next(iter(currencies))

        fields = {
            key: value
            for key, value in fields.items()
            if value is not None and value != ""
        }
        yield fields
