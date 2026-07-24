
import html
import json
import re
from urllib.parse import urljoin



SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _json(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        response = response.text
    return json.loads(response)


def _plain_text(value):
    value = value or ""
    value = re.sub(
        r"(?i)</?(?:address|article|aside|blockquote|div|footer|h[1-6]|header|"
        r"li|main|ol|p|pre|section|table|tr|ul|br|hr)\b[^>]*>",
        "\n",
        value,
    )
    value = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", value)
    value = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", value)
    value = re.sub(r"(?s)<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def crawl(ctx):
    page_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while page_url and page_url not in seen_pages:
        seen_pages.add(page_url)
        page = _json(ctx, page_url)

        for listed in page.get("results") or []:
            job_id = listed.get("id")
            if (
                not job_id
                or job_id in seen_jobs
                or listed.get("is_published") is not True
                or listed.get("is_sample") is True
            ):
                continue
            seen_jobs.add(job_id)

            portal_url = (
                "https://app.dover.com/api/v1/inbound/application-portal-job/"
                + job_id
            )
            description_url = (
                "https://app.dover.com/api/v1/jobs/"
                + job_id
                + "/get_job_description"
            )
            portal = _json(ctx, portal_url)
            detail = _json(ctx, description_url)

            if portal.get("active") is False or portal.get("is_private") is True:
                continue
            if detail.get("is_published") is False:
                continue

            description_html = detail.get("user_provided_description")
            if not description_html:
                description_html = detail.get("user_facing_description")
            description = _plain_text(description_html)
            if not description:
                continue

            raw_locations = portal.get("locations") or listed.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and item.get("name")
            ]
            if not locations:
                continue
            location = locations[0]
            title = portal.get("title") or listed.get("title")
            if not title:
                continue

            apply_url = (
                "https://app.dover.com/apply/caldera/"
                + job_id
                + "/"
            )
            compensation = portal.get("compensation") or {}
            fields = {
                "url": apply_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "source": SITE,
                "external_id": job_id,
                "apply_url": apply_url,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = (
                portal.get("workplace_type") or listed.get("workplace_type")
            )
            if workplace_type:
                fields["workplace_type"] = workplace_type

            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            posted_at = portal.get("created")
            if posted_at:
                fields["posted_at"] = posted_at

            salary_min = compensation.get("lower_bound")
            salary_max = compensation.get("upper_bound")
            if salary_min is not None:
                fields["salary_min"] = salary_min
            if salary_max is not None:
                fields["salary_max"] = salary_max
            if salary_min is not None or salary_max is not None:
                currency = compensation.get("currency_code")
                period = compensation.get("salary_range_type")
                if currency:
                    fields["salary_currency"] = currency
                if period:
                    fields["salary_period"] = period

            yield fields

        next_url = page.get("next")
        page_url = urljoin(page_url, next_url) if next_url else None
