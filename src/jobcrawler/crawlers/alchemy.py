import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/alchemy?includeCompensation=true"
EXTRA_HOSTS = []

COMPANY = "Alchemy"


def _text_response(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, bytes):
        return resp.decode("utf-8", "replace")
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _location(job):
    locations = []
    primary = _clean(job.get("location"))
    if primary:
        locations.append(primary)
    for secondary in job.get("secondaryLocations") or []:
        loc = _clean(secondary.get("location") if isinstance(secondary, dict) else None)
        if loc and loc not in locations:
            locations.append(loc)
    return "; ".join(locations) if locations else None


def _salary(job):
    comp = job.get("compensation") or {}

    summary = _clean(comp.get("scrapeableCompensationSalarySummary"))
    if summary:
        return summary

    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") == "Salary":
            summary = _clean(component.get("summary"))
            if summary:
                return summary

    for tier in comp.get("compensationTiers") or []:
        for component in tier.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("compensationType") == "Salary":
                summary = _clean(component.get("summary"))
                if summary:
                    return summary

    return None


def _put(out, key, value):
    value = _clean(value)
    if value is not None:
        out[key] = value


def crawl(ctx):
    raw = _text_response(ctx.fetch(SITE))
    data = json.loads(raw)
    for job in data.get("jobs") or []:
        if not job.get("isListed", True):
            continue

        description = job.get("descriptionPlain")
        if not description:
            description = unescape(
                re.sub("<[^>]+>", " ", job.get("descriptionHtml") or "")
            )

        out = {
            "url": job.get("jobUrl"),
            "title": job.get("title"),
            "company": COMPANY,
            "location": _location(job),
            "description": description,
            "source": SITE,
        }

        _put(out, "external_id", job.get("id"))
        _put(out, "apply_url", job.get("applyUrl"))
        _put(out, "department", job.get("department") or job.get("team"))
        _put(out, "employment_type", job.get("employmentType"))
        _put(
            out,
            "workplace_type",
            job.get("workplaceType") or ("Remote" if job.get("isRemote") else None),
        )
        _put(out, "posted_at", job.get("publishedAt"))
        _put(out, "salary", _salary(job))

        # Skip only malformed records; Ashby normally supplies all required fields.
        if (
            out.get("url")
            and out.get("title")
            and out.get("location")
            and out.get("description")
        ):
            yield out
