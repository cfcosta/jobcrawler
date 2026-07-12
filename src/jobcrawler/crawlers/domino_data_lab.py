import html
import json
import re


SITE = "https://boards-api.greenhouse.io/v1/boards/dominodatalab/jobs?content=true"
EXTRA_HOSTS = []


def _plain_text(value):
    value = html.unescape(value or "")
    value = re.sub(
        r"<(?:br|hr)\s*/?>|</(?:p|div|li|ul|ol|h[1-6]|section|article|tr)>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _printed_salary(encoded_content):
    decoded = html.unescape(encoded_content or "")
    match = re.search(
        r'<div[^>]*class=["\'][^"\']*\bpay-range\b[^"\']*["\'][^>]*>'
        r"(.*?)</div>",
        decoded,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return _plain_text(match.group(1))
    return None


def crawl(ctx):
    payload = json.loads(ctx.fetch(SITE + "&pay_transparency=true"))

    for posting in payload.get("jobs", []):
        data = {
            "url": posting.get("absolute_url"),
            "apply_url": posting.get("absolute_url"),
            "title": posting.get("title"),
            "company": "Domino Data Lab",
            "location": (posting.get("location") or {}).get("name"),
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
            for office in (posting.get("offices") or [])
            if office.get("name")
        ]
        if offices:
            data["locations"] = offices

        departments = [
            department.get("name")
            for department in (posting.get("departments") or [])
            if department.get("name")
        ]
        if departments:
            data["department"] = ", ".join(departments)

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
                data["salary_min"] = min(minimums)
            if maximums:
                data["salary_max"] = max(maximums)
            data["salary_currency"] = next(iter(currencies))

            blurbs = " ".join(
                _plain_text(item.get("blurb"))
                for item in ranges
                if item.get("blurb")
            ).lower()
            if "annual" in blurbs:
                data["salary_period"] = "annual"

            printed = _printed_salary(posting.get("content"))
            if printed:
                data["salary"] = printed

        yield {
            key: value
            for key, value in data.items()
            if value is not None
        }
