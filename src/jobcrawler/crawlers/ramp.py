import json
import re
from datetime import datetime, timezone

SITE = "https://api.ashbyhq.com/posting-api/job-board/ramp?includeCompensation=true"


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


def _salary_text(compensation):
    """Flatten the first Salary summaryComponent to text, falling back to the
    scrapeable summary text when no component carries bounds."""
    if not isinstance(compensation, dict):
        return None
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") != "Salary":
            continue
        low = component.get("minValue")
        high = component.get("maxValue")
        if low is None and high is None:
            continue
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if low is not None and high is not None:
            amount = "%s - %s" % (low, high)
        else:
            amount = str(low if low is not None else high)
        text = "%s %s" % (currency, amount) if currency else amount
        if interval:
            text = "%s / %s" % (text, interval)
        return text
    return compensation.get("scrapeableCompensationSalarySummary") or None


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


def crawl(ctx):
    """Yield Job dicts for every open Ramp role via the Ashby board API."""
    resp = ctx.fetch(SITE)
    data = json.loads(resp)
    jobs = data.get("jobs", [])

    for j in jobs:
        # Skip unlisted jobs
        if not j.get("isListed", True):
            continue

        job_url = j.get("jobUrl", "")
        title = j.get("title", "")
        description = j.get("descriptionPlain", "") or ""

        # Compensation: flattened text plus the structured quartet
        comp = j.get("compensation")
        salary = _salary_text(comp)

        # Employment type
        employment_type = j.get("employmentType") or None

        # Workplace type: prefer workplaceType over isRemote
        workplace_type = j.get("workplaceType") or None
        if not workplace_type and j.get("isRemote"):
            workplace_type = "Remote"

        # Location
        location = j.get("location", "") or ""

        # Department
        department = j.get("department") or j.get("team") or None

        # Posted date
        posted_at = None
        pub = j.get("publishedAt")
        if pub:
            try:
                # Format: 2026-06-11T17:21:26.410+00:00
                posted_at = pub
            except Exception:
                posted_at = None

        # External ID
        external_id = j.get("id") or None

        # Apply URL
        apply_url = j.get("applyUrl") or None

        job = {
            "url": job_url,
            "title": title,
            "company": "Ramp",
            "location": location,
            "description": description,
            "salary": salary,
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "department": department,
            "posted_at": posted_at,
            "expires_at": None,
            "external_id": external_id,
            "apply_url": apply_url,
            "source": SITE,
        }
        for key, value in _structured_salary(comp).items():
            job[key] = value
        yield job
