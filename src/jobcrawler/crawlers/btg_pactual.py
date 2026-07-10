
import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/btgpactual/jobs?content=true"
EXTRA_HOSTS = []


def text_from_html(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"(?i)<\s*(br|hr)\s*/?\s*>|<\s*/\s*(p|div|li|ul|ol|h[1-6]|tr|table|blockquote)\s*>",
        "\n",
        value,
    )
    value = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in data.get("jobs", []):
        title = posting.get("title")
        location = (posting.get("location") or {}).get("name")
        url = posting.get("absolute_url")
        description = text_from_html(posting.get("content"))

        if not title or not location or not url or not description:
            continue

        offices = [
            office["name"]
            for office in posting.get("offices") or []
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in posting.get("departments") or []
            if department.get("name")
        ]

        job = {
            "url": url,
            "apply_url": url,
            "title": title,
            "company": posting.get("company_name") or "BTG Pactual",
            "location": location,
            "description": description,
            "external_id": str(posting["id"]),
            "source": SITE,
        }

        if offices:
            job["locations"] = offices
        if departments:
            job["department"] = ", ".join(departments)
        if posting.get("first_published"):
            job["posted_at"] = posting["first_published"]
        if posting.get("application_deadline"):
            job["expires_at"] = posting["application_deadline"]

        pay_ranges = posting.get("pay_input_ranges") or []
        currencies = {
            pay_range.get("currency_type")
            for pay_range in pay_ranges
            if pay_range.get("currency_type")
        }
        if pay_ranges and len(currencies) == 1:
            minimums = [
                pay_range["min_cents"] / 100
                for pay_range in pay_ranges
                if pay_range.get("min_cents") is not None
            ]
            maximums = [
                pay_range["max_cents"] / 100
                for pay_range in pay_ranges
                if pay_range.get("max_cents") is not None
            ]
            if minimums:
                job["salary_min"] = min(minimums)
            if maximums:
                job["salary_max"] = max(maximums)
            job["salary_currency"] = next(iter(currencies))

        yield job
