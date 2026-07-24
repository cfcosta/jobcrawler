
import json
import re
from html import unescape

SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


def _text(value):
    if not value:
        return None
    value = unescape(value)
    value = re.sub(r"(?i)<br\s*/?>|</p\s*>|</li\s*>|</h[1-6]\s*>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n\n", value)
    return value.strip() or None


def crawl(ctx):
    url = SITE
    while url:
        page = json.loads(ctx.fetch(url))
        for summary in page.get("results", []):
            if not summary.get("is_published") or summary.get("is_sample"):
                continue
            job_id = summary.get("id")
            if not job_id:
                continue
            apply_url = "https://app.dover.com/apply/DeepSource/%s/" % job_id
            detail_url = "https://app.dover.com/api/v1/inbound/application-portal-job/%s" % job_id
            detail = json.loads(ctx.fetch(detail_url))
            if not detail.get("active") or detail.get("is_private"):
                continue
            names = [x.get("name") for x in detail.get("locations", []) if x.get("name")]
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": detail.get("title") or summary.get("title"),
                "company": "DeepSource",
                "location": names[0] if names else None,
                "locations": names or None,
                "description": _text(detail.get("user_provided_description")),
                "workplace_type": detail.get("workplace_type"),
                "posted_at": detail.get("created"),
                "external_id": job_id,
                "source": SITE,
            }
            compensation = detail.get("compensation") or {}
            if compensation.get("employment_type"):
                fields["employment_type"] = compensation["employment_type"]
            if compensation.get("open_to_sharing_comp"):
                if compensation.get("lower_bound") is not None:
                    fields["salary_min"] = compensation["lower_bound"]
                if compensation.get("upper_bound") is not None:
                    fields["salary_max"] = compensation["upper_bound"]
                if compensation.get("currency_code") and ("salary_min" in fields or "salary_max" in fields):
                    fields["salary_currency"] = compensation["currency_code"]
                    fields["salary_period"] = compensation.get("salary_range_type")
            yield {key: value for key, value in fields.items() if value is not None}
        url = page.get("next")
