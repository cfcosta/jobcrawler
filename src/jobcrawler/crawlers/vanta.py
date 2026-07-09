
import json
import re

SITE = "https://api.ashbyhq.com/posting-api/job-board/vanta?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    value = value.strip()
    return value or None


def _salary(compensation):
    if not isinstance(compensation, dict):
        return None

    parts = []
    for component in compensation.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        comp_type = component.get("compensationType")
        min_value = component.get("minValue")
        max_value = component.get("maxValue")
        currency = component.get("currencyCode")
        interval = component.get("interval")
        if min_value is None and max_value is None:
            continue
        label = comp_type or "Compensation"
        if min_value is not None and max_value is not None:
            amount = f"{min_value:g} - {max_value:g}"
        else:
            amount = f"{(min_value if min_value is not None else max_value):g}"
        bits = [label, amount]
        if currency:
            bits.append(currency)
        if interval:
            bits.append(interval)
        parts.append(" ".join(bits))

    if parts:
        return "; ".join(parts)

    return _clean(
        compensation.get("scrapeableCompensationSalarySummary")
        or compensation.get("compensationTierSummary")
    )


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


def crawl(ctx):
    data = json.loads(ctx.fetch(SITE))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (title and url and location and description):
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Vanta",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional_fields = {
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")),
            "department": _clean(job.get("department") or job.get("departmentName") or job.get("team")),
            "posted_at": _clean(job.get("publishedAt")),
            "salary": _salary(job.get("compensation")),
        }
        for key, value in optional_fields.items():
            if value is not None:
                item[key] = value
        for key, value in _structured_salary(job.get("compensation")).items():
            item[key] = value

        yield item
