"""Crawler for SafetyWing's Pinpoint job board."""

import html
import json
import re

SITE = "https://safetywing.pinpointhq.com/postings.json"
EXTRA_HOSTS = []
COMPANY = "SafetyWing"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(html_text):
    """Unescape HTML entities then strip tags, normalizing whitespace.

    Pinpoint packs list items with no whitespace between tags
    (``</li><li>``), so tags are replaced with a space to avoid merging
    adjacent words before the whitespace collapse.
    """
    if not html_text:
        return ""
    text = html.unescape(html_text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


# (header_key, body_key) sections assembled, in order, into the description.
_SECTIONS = (
    (None, "description"),
    ("key_responsibilities_header", "key_responsibilities"),
    ("skills_knowledge_expertise_header", "skills_knowledge_expertise"),
    ("benefits_header", "benefits"),
)


def _build_description(job):
    """Join the posting's HTML content fields into one plain-text blob."""
    parts = []
    for header_key, body_key in _SECTIONS:
        body = _strip_tags(job.get(body_key))
        if not body:
            continue
        header = _strip_tags(job.get(header_key)) if header_key else ""
        if header:
            parts.append(header)
        parts.append(body)
    return "\n\n".join(parts)


def crawl(ctx):
    """Yield one Job dict per open role from SafetyWing's Pinpoint board.

    The public ``postings.json`` feed returns the whole board as
    ``{"data": [ ... ]}`` in a single GET.
    """
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("data", []):
        title = job.get("title")
        url = job.get("url")
        description = _build_description(job)
        if not (title and url and description):
            continue

        loc = job.get("location") or {}
        location = ", ".join(
            part.strip()
            for part in (loc.get("city"), loc.get("province"), loc.get("name"))
            if part and part.strip()
        )
        if not location:
            location = job.get("workplace_type_text") or "Remote"

        out = {
            "url": url,
            "title": title,
            "company": COMPANY,
            "location": location,
            "description": description,
            "source": SITE,
        }

        external_id = job.get("id")
        if external_id:
            out["external_id"] = str(external_id)

        out["apply_url"] = url.rstrip("/") + "/applications/new"

        department = (job.get("job") or {}).get("department") or {}
        dept_name = (department.get("name") or "").strip()
        if dept_name:
            out["department"] = dept_name

        employment_type = job.get("employment_type_text") or job.get("employment_type")
        if employment_type:
            out["employment_type"] = employment_type

        workplace_type = job.get("workplace_type")
        if workplace_type:
            out["workplace_type"] = workplace_type

        deadline = job.get("deadline_at")
        if deadline:
            out["expires_at"] = deadline

        if job.get("compensation_visible"):
            summary = job.get("compensation")
            if isinstance(summary, str) and summary.strip():
                out["salary"] = _strip_tags(summary)
            comp_min = job.get("compensation_minimum")
            comp_max = job.get("compensation_maximum")
            currency = job.get("compensation_currency")
            frequency = job.get("compensation_frequency")
            if comp_min is not None:
                out["salary_min"] = comp_min
            if comp_max is not None:
                out["salary_max"] = comp_max
            if currency:
                out["salary_currency"] = currency
            if frequency:
                out["salary_period"] = frequency

        yield out
