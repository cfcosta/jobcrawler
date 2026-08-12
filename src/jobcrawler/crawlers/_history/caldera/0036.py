import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse
from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _json(response):
    if isinstance(response, str):
        return json.loads(response)
    return response


def _text(value):
    if not value:
        return None
    text = re.sub(
        r"<\s*(?:br|/p|/div|/li|/h[1-6]|/tr|/section|/article)\s*/?\s*>",
        "\n",
        str(value),
        flags=re.I,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    result = "\n".join(line for line in lines if line)
    return result or None


def _same_host(url):
    parsed = urlparse(url)
    site = urlparse(SITE)
    return (
        parsed.scheme in ("http", "https")
        and parsed.hostname == site.hostname
    )


def _location_names(detail, record):
    values = detail.get("locations")
    if not isinstance(values, list):
        values = record.get("locations")
    names = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
    return names


def crawl(ctx):
    next_url = SITE
    seen_pages = set()

    while next_url and next_url not in seen_pages and _same_host(next_url):
        seen_pages.add(next_url)
        page = _json(ctx.fetch(next_url))
        if not isinstance(page, dict):
            return

        for record in page.get("results") or []:
            if not isinstance(record, dict):
                continue
            if record.get("is_published") is not True or record.get("is_sample"):
                continue

            external_id = record.get("id")
            if not external_id:
                continue

            detail_url = (
                "https://app.dover.com/api/v1/inbound/"
                "application-portal-job/" + str(external_id)
            )
            detail = _json(ctx.fetch(detail_url))
            if not isinstance(detail, dict):
                continue
            if detail.get("active") is not True or detail.get("is_private"):
                continue

            title = detail.get("title") or record.get("title")
            description = _text(detail.get("user_provided_description"))
            if not title or not description:
                continue

            locations = _location_names(detail, record)
            workplace_type = (
                detail.get("workplace_type") or record.get("workplace_type")
            )
            location = (
                detail.get("location")
                or (locations[0] if locations else None)
                or workplace_type
            )
            if not location:
                continue

            apply_url = (
                "https://app.dover.com/apply/caldera/"
                + str(external_id)
                + "/"
            )
            fields = {
                "source": SITE,
                "url": apply_url,
                "title": title,
                "company": "Caldera",
                "location": location,
                "description": description,
                "external_id": str(external_id),
                "apply_url": apply_url,
            }

            if locations:
                fields["locations"] = locations
            if workplace_type:
                fields["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            compensation = detail.get("compensation")
            if isinstance(compensation, dict):
                employment_type = compensation.get("employment_type")
                if employment_type:
                    fields["employment_type"] = employment_type

                minimum = compensation.get("lower_bound")
                maximum = compensation.get("upper_bound")
                if minimum is not None:
                    fields["salary_min"] = minimum
                if maximum is not None:
                    fields["salary_max"] = maximum

                if minimum is not None or maximum is not None:
                    currency = compensation.get("currency_code")
                    period = compensation.get("salary_range_type")
                    if currency:
                        fields["salary_currency"] = currency
                    if period:
                        fields["salary_period"] = period

            compensation_text = detail.get("compensation_details")
            if compensation_text:
                fields["salary"] = compensation_text

            yield Job(**fields)

        following = page.get("next")
        if not following:
            return
        next_url = urljoin(next_url, following)
