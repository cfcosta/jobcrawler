import html
import json
import re



SITE = "https://boards-api.greenhouse.io/v1/boards/solarisbank/jobs?content=true"
EXTRA_HOSTS = []


def _clean_html(value):
    text = html.unescape(html.unescape(value or ""))
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/h[1-6]|/section|/tr|/ul|/ol)\s*/?\s*>",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    response = ctx.fetch(SITE + "&pay_transparency=true")
    payload = json.loads(response)

    for record in payload.get("jobs", []):
        title = (record.get("title") or "").strip()
        location = (record.get("location") or {}).get("name")
        description = _clean_html(record.get("content"))
        url = record.get("absolute_url")
        if not title or not location or not description or not url:
            continue

        fields = {
            "url": url,
            "source": SITE,
            "title": title,
            "company": record.get("company_name") or "Solaris",
            "location": location,
            "description": description,
            "external_id": str(record.get("requisition_id") or record["id"]),
        }

        offices = [
            office["name"]
            for office in (record.get("offices") or [])
            if office.get("name")
        ]
        if offices:
            fields["locations"] = offices

        departments = [
            department["name"]
            for department in (record.get("departments") or [])
            if department.get("name")
        ]
        if departments:
            fields["department"] = ", ".join(departments)

        if record.get("first_published"):
            fields["posted_at"] = record["first_published"]
        if record.get("application_deadline"):
            fields["expires_at"] = record["application_deadline"]

        ranges = record.get("pay_input_ranges") or []
        currencies = {
            pay.get("currency_type")
            for pay in ranges
            if pay.get("currency_type")
        }
        if ranges and len(currencies) == 1:
            minimums = [
                pay["min_cents"] / 100
                for pay in ranges
                if pay.get("min_cents") is not None
            ]
            maximums = [
                pay["max_cents"] / 100
                for pay in ranges
                if pay.get("max_cents") is not None
            ]
            if minimums:
                fields["salary_min"] = min(minimums)
            if maximums:
                fields["salary_max"] = max(maximums)
            fields["salary_currency"] = next(iter(currencies))
            fields["salary_period"] = "1 YEAR"

        yield fields
