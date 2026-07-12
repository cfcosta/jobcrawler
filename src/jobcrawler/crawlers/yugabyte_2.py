
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/yugabyte/jobs?content=true"
EXTRA_HOSTS = []


def _description(value):
    decoded = html.unescape(value or "")
    decoded = re.sub(
        r"(?i)<\s*(?:br|hr)\s*/?\s*>|<\s*/?\s*(?:address|article|aside|blockquote|div|dl|dt|dd|figcaption|figure|footer|h[1-6]|header|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
        "\n",
        decoded,
    )
    decoded = re.sub(r"(?is)<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", decoded)
    decoded = re.sub(r"(?s)<[^>]+>", " ", decoded)
    decoded = html.unescape(decoded)
    lines = (" ".join(line.split()) for line in decoded.splitlines())
    return "\n".join(line for line in lines if line)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for posting in payload.get("jobs", []):
        location = (posting.get("location") or {}).get("name")
        description = _description(posting.get("content"))
        if not posting.get("title") or not location or not description:
            continue

        fields = {
            "url": posting["absolute_url"],
            "title": posting["title"].strip(),
            "company": "YugabyteDB",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
            "source": SITE,
        }

        offices = [
            office["name"]
            for office in posting.get("offices", [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in posting.get("departments", [])
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
