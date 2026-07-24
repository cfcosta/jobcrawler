import re
import json

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _plain_text(value):
    text = value or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(
        r"(?i)</?(?:p|div|h[1-6]|li|ul|ol|section|article|blockquote|pre|tr|table)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    replacements = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": "\"",
        "&#39;": "'",
        "&apos;": "'",
        "&nbsp;": " ",
    }
    for encoded, decoded in replacements.items():
        text = text.replace(encoded, decoded)

    lines = []
    for line in text.splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def crawl(ctx):
    page_url = SITE

    while page_url:
        page = json.loads(ctx.fetch(page_url))

        for listed in page.get("results", []):
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            apply_url = "https://app.dover.com/apply/caldera/" + job_id + "/"
            detail_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            detail = json.loads(ctx.fetch(detail_url))

            if not detail.get("active") or detail.get("is_private"):
                continue

            description = _plain_text(detail.get("user_provided_description"))
            title = detail.get("title") or listed.get("title")
            if not title or not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = []
            for item in raw_locations:
                if isinstance(item, dict) and item.get("name"):
                    locations.append(item["name"])

            location = detail.get("location")
            if not location and locations:
                location = locations[0]
            if not location:
                location = "Remote"

            kwargs = {
                "url": apply_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": "Dover",
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                kwargs["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                kwargs["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                kwargs["employment_type"] = employment_type

            created = detail.get("created")
            if created:
                kwargs["posted_at"] = created

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if lower is not None or upper is not None:
                if lower is not None:
                    kwargs["salary_min"] = lower
                if upper is not None:
                    kwargs["salary_max"] = upper
                currency = compensation.get("currency_code")
                if currency:
                    kwargs["salary_currency"] = currency
                period = compensation.get("salary_range_type")
                if period:
                    kwargs["salary_period"] = period

            yield Job(**kwargs)

        page_url = page.get("next")
