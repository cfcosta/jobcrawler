
import html
import json
import re

from jobcrawler.runtime.contract import Job


SITE = "https://app.dover.com/api/v1/careers-page/beead238-de58-4e47-8bc4-b3b45b1f55d6/jobs?limit=50&offset=0"
EXTRA_HOSTS = []

COMPANY = "Caldera"
APPLY_BASE = "https://app.dover.com/apply/caldera/"
DETAIL_BASE = "https://app.dover.com/api/v1/inbound/application-portal-job/"


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    if hasattr(value, "json"):
        return value.json()
    return json.loads(value.text)


def _text(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|h[1-6]|li|ul|ol)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def crawl(ctx):
    url = SITE

    while url:
        page = _json(ctx.fetch(url))
        for listed in page.get("results", []):
            if not listed.get("is_published") or listed.get("is_sample"):
                continue

            job_id = listed.get("id")
            if not job_id:
                continue

            detail = _json(ctx.fetch(DETAIL_BASE + job_id))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            description = _text(detail.get("user_provided_description"))
            if not description:
                continue

            raw_locations = detail.get("locations") or listed.get("locations") or []
            locations = [
                item["name"].strip()
                for item in raw_locations
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item["name"].strip()
            ]
            primary = detail.get("location")
            if not isinstance(primary, str) or not primary.strip():
                primary = locations[0] if locations else None

            if not primary:
                continue

            fields = {
                "url": APPLY_BASE + job_id + "/",
                "apply_url": APPLY_BASE + job_id + "/",
                "title": detail.get("title") or listed.get("title"),
                "company": COMPANY,
                "location": primary,
                "source": SITE,
                "description": description,
                "external_id": job_id,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = detail.get("workplace_type") or listed.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            currency = compensation.get("currency_code")
            period = compensation.get("salary_range_type")
            if lower is not None:
                fields["salary_min"] = lower
            if upper is not None:
                fields["salary_max"] = upper
            if (lower is not None or upper is not None) and currency:
                fields["salary_currency"] = currency
            if (lower is not None or upper is not None) and period:
                fields["salary_period"] = period

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            yield Job(**fields)

        url = page.get("next")
