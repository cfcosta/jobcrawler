import json
import re
from urllib.parse import quote, urljoin

SITE = "https://findweb3.com/jobs/engineering"
EXTRA_HOSTS = []

_API = "https://findweb3.com/api/jobs?perPage=31&offset=0&categoryPath=engineering"
_BUILD_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')


def _text(response):
    return response.text if hasattr(response, "text") else str(response)


def _apply_url(value):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("mailto:"):
        return value
    if "@" in value and not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        return "mailto:" + value
    if not re.match(r"^https?://", value, re.I):
        return "https://" + value.lstrip("/")
    return value


def _employment_type(value):
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return " / ".join(values) or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def crawl(ctx):
    listing = _text(ctx.fetch(SITE))
    build_match = _BUILD_RE.search(listing)
    if not build_match:
        return
    build_id = build_match.group(1)

    payload = json.loads(_text(ctx.fetch(_API)))
    seen = set()
    for summary in payload.get("records") or []:
        record_id = summary.get("id")
        if not record_id or record_id in seen:
            continue
        seen.add(record_id)

        encoded_id = quote(str(record_id), safe="")
        data_url = urljoin(
            SITE, "/_next/data/" + quote(build_id, safe="") +
            "/job/" + encoded_id + ".json"
        )
        detail = json.loads(_text(ctx.fetch(data_url)))
        page_props = detail.get("pageProps") or {}
        if page_props.get("error"):
            continue
        job = page_props.get("job") or {}
        raw = job.get("_rawJson") or {}
        fields = raw.get("fields") or job.get("fields") or {}

        title = fields.get("Job Title")
        company = fields.get("Company Name")
        location = fields.get("Company HQ")
        description = fields.get("Job Description")
        if not all(isinstance(value, str) and value.strip() for value in (
            title, company, location, description
        )):
            continue

        workplace_type = None
        remote = fields.get("Fully Remote?")
        if isinstance(remote, str) and remote.strip().lower() == "yes":
            workplace_type = "remote"

        job_url = urljoin(SITE, "/job/" + encoded_id)
        values = {
            "url": job_url,
            "title": title.strip(),
            "company": company.strip(),
            "location": location.strip(),
            "description": description.strip(),
            "source": SITE,
            "external_id": str(record_id),
            "posted_at": fields.get("Posted Date") or None,
            "employment_type": _employment_type(fields.get("Job Type")),
            "workplace_type": workplace_type,
            "department": fields.get("Category") or None,
            "salary": fields.get("Pay") or None,
            "apply_url": _apply_url(fields.get("How to Apply")),
        }
        yield {key: value for key, value in values.items() if value is not None}
