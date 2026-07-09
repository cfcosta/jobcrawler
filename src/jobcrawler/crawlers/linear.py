import json
from datetime import datetime

SITE = "https://api.ashbyhq.com/posting-api/job-board/linear?includeCompensation=true"


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
    The interval is passed as stated into salary_period (no defaults here; the
    text builder's USD/year fallbacks apply to the text only).
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
    resp = ctx.fetch(SITE)
    data = json.loads(resp)
    jobs = data.get("jobs", [])
    for job in jobs:
        if not job.get("isListed", True):
            continue

        title = job.get("title", "")
        if not title:
            continue

        job_url = job.get("jobUrl", "")
        if not job_url:
            continue

        description = job.get("descriptionPlain", "")
        if not description:
            description = job.get("descriptionHtml", "")

        company = "Linear"

        location = job.get("location", "")

        employment_type = job.get("employmentType")
        workplace_type = job.get("workplaceType")

        department = job.get("department")
        if job.get("team") and department:
            department = department + " - " + job.get("team")
        elif job.get("team"):
            department = job.get("team")

        posted_at = job.get("publishedAt")

        external_id = job.get("id")
        apply_url = job.get("applyUrl")

        salary = None
        comp = job.get("compensation", {})
        sc = comp.get("summaryComponents", [])
        if sc:
            for component in sc:
                min_val = component.get("minValue")
                max_val = component.get("maxValue")
                currency = component.get("currencyCode", "USD")
                interval = component.get("interval", "year")
                if min_val is not None and max_val is not None:
                    salary = f"{currency} {min_val}-{max_val}/{interval}"
                    break

        out = {
            "url": job_url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "source": SITE,
            "employment_type": employment_type,
            "workplace_type": workplace_type,
            "department": department,
            "posted_at": posted_at,
            "salary": salary,
            "external_id": external_id,
            "apply_url": apply_url,
        }
        for key, value in _structured_salary(comp).items():
            out[key] = value
        yield out
