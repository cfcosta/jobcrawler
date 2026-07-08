
import html
import json
import re
from urllib.parse import urljoin

SITE = "https://apply.workable.com/api/v1/widget/accounts/trailofbits"
EXTRA_HOSTS = []

COMPANY = "Trail of Bits"
ACCOUNT = "trailofbits"


def _fetch_text(ctx, url):
    response = ctx.fetch(url)
    if hasattr(response, "text"):
        return response.text
    return response


def _clean_html(value):
    if not value:
        return None
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*(p|div|li|h[1-6]|ul|ol|section|article)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or None


def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
        elif value:
            return value
    return None


def _location_from_job(job):
    parts = []
    locations = job.get("locations") or []
    visible_locations = [loc for loc in locations if isinstance(loc, dict) and not loc.get("hidden")]
    if visible_locations:
        labels = []
        for loc in visible_locations:
            loc_parts = []
            for key in ("city", "region", "state", "country"):
                value = loc.get(key)
                if value and value not in loc_parts:
                    loc_parts.append(str(value))
            if loc_parts:
                labels.append(", ".join(loc_parts))
        if labels:
            return "; ".join(labels)

    location = job.get("location")
    if isinstance(location, dict):
        for key in ("city", "region", "state", "country"):
            value = location.get(key)
            if value and value not in parts:
                parts.append(str(value))
    else:
        for key in ("city", "state", "country"):
            value = job.get(key)
            if value and value not in parts:
                parts.append(str(value))

    if parts:
        return ", ".join(parts)
    if job.get("remote") or job.get("telecommuting"):
        return "Remote"
    return "United States"


def _department(value):
    if isinstance(value, list):
        return ", ".join(str(v).strip() for v in value if str(v).strip()) or None
    if isinstance(value, str):
        return value.strip() or None
    return None


def _employment_type(job):
    value = _first_nonempty(job.get("employment_type"), job.get("type"))
    if value == "full":
        return "Full-time"
    if value == "part":
        return "Part-time"
    return value


def _workplace_type(job):
    value = _first_nonempty(job.get("workplace"))
    if value:
        return value
    if job.get("remote") is True or job.get("telecommuting") is True:
        return "remote"
    return None


def crawl(ctx):
    account = json.loads(_fetch_text(ctx, SITE))
    for summary in account.get("jobs") or []:
        shortcode = summary.get("shortcode")
        if not shortcode:
            continue

        detail_url = f"https://apply.workable.com/api/v1/accounts/{ACCOUNT}/jobs/{shortcode}"
        detail = json.loads(_fetch_text(ctx, detail_url))
        if detail.get("state") and detail.get("state") != "published":
            continue

        chunks = []
        for key in ("description", "requirements", "benefits"):
            text = _clean_html(detail.get(key))
            if text:
                chunks.append(text)
        description = "\n\n".join(chunks).strip()
        if not description:
            continue

        job = {
            "url": _first_nonempty(summary.get("url"), f"https://apply.workable.com/j/{shortcode}"),
            "title": _first_nonempty(detail.get("title"), summary.get("title")),
            "company": COMPANY,
            "location": _location_from_job(detail) or _location_from_job(summary),
            "description": description,
            "source": SITE,
            "external_id": str(_first_nonempty(detail.get("id"), shortcode)),
            "apply_url": _first_nonempty(summary.get("application_url"), f"https://apply.workable.com/j/{shortcode}/apply"),
            "employment_type": _employment_type({**summary, **detail}),
            "workplace_type": _workplace_type({**summary, **detail}),
            "department": _department(_first_nonempty(detail.get("department"), summary.get("department"))),
            "posted_at": _first_nonempty(detail.get("published"), summary.get("published_on")),
        }

        yield {key: value for key, value in job.items() if value is not None and value != ""}
