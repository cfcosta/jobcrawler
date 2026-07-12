
import html
import json
import re

SITE = "https://boards-api.greenhouse.io/v1/boards/imply/jobs?content=true"
EXTRA_HOSTS = []


def clean_html(value):
    value = html.unescape(html.unescape(value or ""))
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(
        r"(?i)</(?:p|li|ul|ol|h[1-6]|div|section|article)>",
        "\n",
        value,
    )
    value = re.sub(r"(?i)<li(?:\s[^>]*)?>", "- ", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    api_url = SITE + "&pay_transparency=true"
    response = ctx.fetch(api_url)
    raw = response.text if hasattr(response, "text") else response
    data = json.loads(raw)
    jobs = data.get("jobs") or []

    total = (data.get("meta") or {}).get("total")
    if total is not None and len(jobs) != total:
        raise RuntimeError(
            "Incomplete Greenhouse response: expected %s jobs, received %s"
            % (total, len(jobs))
        )

    for posting in jobs:
        location = (posting.get("location") or {}).get("name")
        description = clean_html(posting.get("content"))
        offices = [
            office["name"]
            for office in (posting.get("offices") or [])
            if office.get("name")
        ]
        departments = [
            department["name"]
            for department in (posting.get("departments") or [])
            if department.get("name")
        ]

        job = {
            "url": posting["absolute_url"],
            "title": posting["title"],
            "company": "Imply",
            "location": location,
            "description": description,
            "source": SITE,
            "external_id": str(posting["id"]),
            "apply_url": posting["absolute_url"],
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
