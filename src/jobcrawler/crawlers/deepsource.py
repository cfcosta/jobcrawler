
"""Crawler for the DeepSource careers board served by Dover."""

import html
import json
import re
from urllib.parse import urljoin, urlparse


SITE = "https://app.dover.com/api/v1/careers-page/02322923-27a3-439d-aadc-865046d47d15/jobs?limit=50&offset=0"
EXTRA_HOSTS = []


_BLOCK_RE = re.compile(
    r"</?(?:p|div|section|article|h[1-6]|ul|ol|li|br|blockquote|pre|tr|td|th)\b[^>]*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)+")
_SPACE_RE = re.compile(r"[ \t\f\v]+")


def _load_json(response):
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, bytes):
        response = response.decode("utf-8")
    if isinstance(response, str):
        return json.loads(response)

    value = response.json()
    return value


def _html_to_text(value):
    if not isinstance(value, str) or not value.strip():
        return None

    value = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", value)
    value = _BLOCK_RE.sub("\n", value)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value).replace("\r", "")
    value = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in value.split("\n"))
    value = _BLANK_RE.sub("\n\n", value).strip()
    return value or None


def _same_host(url):
    return urlparse(url).hostname == urlparse(SITE).hostname


def crawl(ctx):
    next_url = SITE
    seen_pages = set()
    seen_jobs = set()

    while next_url and next_url not in seen_pages:
        if not _same_host(next_url):
            raise ValueError("Dover pagination unexpectedly changed hosts")

        seen_pages.add(next_url)
        page = _load_json(ctx.fetch(next_url))

        for summary in page.get("results") or []:
            job_id = summary.get("id")
            if (
                not job_id
                or job_id in seen_jobs
                or summary.get("is_published") is not True
                or summary.get("is_sample") is True
            ):
                continue

            detail_url = urljoin(SITE, f"/api/v1/inbound/application-portal-job/{job_id}")
            detail = _load_json(ctx.fetch(detail_url))
            if detail.get("active") is False or detail.get("is_private") is True:
                continue

            title = detail.get("title") or summary.get("title")
            description = _html_to_text(detail.get("user_provided_description"))
            raw_locations = detail.get("locations") or summary.get("locations") or []
            locations = [
                item.get("name")
                for item in raw_locations
                if isinstance(item, dict) and isinstance(item.get("name"), str)
                and item.get("name").strip()
            ]
            location = locations[0] if locations else detail.get("location")
            if not title or not description or not location:
                continue

            seen_jobs.add(job_id)
            apply_url = urljoin(SITE, f"/apply/DeepSource/{job_id}/")
            fields = {
                "url": apply_url,
                "apply_url": apply_url,
                "title": html.unescape(title).strip(),
                "company": detail.get("client_name") or "DeepSource",
                "location": location,
                "description": description,
                "external_id": job_id,
                "source": SITE,
            }

            if locations:
                fields["locations"] = locations

            workplace_type = detail.get("workplace_type") or summary.get("workplace_type")
            if workplace_type:
                fields["workplace_type"] = workplace_type

            created = detail.get("created")
            if created:
                fields["posted_at"] = created

            compensation = detail.get("compensation") or {}
            employment_type = compensation.get("employment_type")
            if employment_type:
                fields["employment_type"] = employment_type

            lower = compensation.get("lower_bound")
            upper = compensation.get("upper_bound")
            if compensation.get("open_to_sharing_comp") is True and (
                lower is not None or upper is not None
            ):
                if lower is not None:
                    fields["salary_min"] = lower
                if upper is not None:
                    fields["salary_max"] = upper
                if compensation.get("currency_code"):
                    fields["salary_currency"] = compensation["currency_code"]
                if compensation.get("salary_range_type"):
                    fields["salary_period"] = compensation["salary_range_type"]

            yield fields

        candidate = page.get("next")
        next_url = urljoin(next_url, candidate) if candidate else None
