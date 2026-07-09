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


def _positive_amount(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None


def _structured_salary(compensation):
    """Structured quartet from Salary summaryComponents (§4.6 dual capture).

    Returns {} when no Salary component carries usable bounds, or when components
    disagree on currency — the flattened text still flows to the host parser.
    The interval is passed as stated into salary_period; the host maps it.
    """
    if not isinstance(compensation, dict):
        return {}
    mins = []
    maxes = []
    currencies = set()
    intervals = set()
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        low = _positive_amount(component.get("minValue"))
        high = _positive_amount(component.get("maxValue"))
        if low is None and high is None:
            continue
        if low is not None:
            mins.append(low)
        if high is not None:
            maxes.append(high)
        currency = component.get("currencyCode")
        if currency:
            currencies.add(str(currency))
        interval = component.get("interval")
        if interval:
            intervals.add(str(interval))
    if (not mins and not maxes) or len(currencies) > 1:
        return {}
    fields = {}
    if mins:
        fields["salary_min"] = min(mins)
    if maxes:
        fields["salary_max"] = max(maxes)
    if currencies:
        fields["salary_currency"] = currencies.pop()
    if len(intervals) == 1:
        fields["salary_period"] = intervals.pop()
    return fields


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
        for key, value in _structured_salary(job.get("compensation")).items():
            out[key] = value

        # Skip only malformed records; Ashby normally supplies all required fields.
        if (
            out.get("url")
            and out.get("title")
            and out.get("location")
            and out.get("description")
        ):
            yield out
