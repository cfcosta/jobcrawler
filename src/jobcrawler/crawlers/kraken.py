
import json
import re
from html import unescape

SITE = "https://api.ashbyhq.com/posting-api/job-board/kraken.com?includeCompensation=true"
EXTRA_HOSTS = []


def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _html_to_text(html):
    if not html:
        return None
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    text = re.sub(r"(?i)</\s*(p|div|li|ul|ol|h1|h2|h3|h4)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() or None


def _location(job):
    locations = []
    primary = _clean(job.get("location"))
    if primary:
        locations.append(primary)
    for item in job.get("secondaryLocations") or []:
        loc = _clean(item.get("location") if isinstance(item, dict) else item)
        if loc and loc not in locations:
            locations.append(loc)
    return "; ".join(locations) if locations else None


def _format_amount(value):
    if isinstance(value, (int, float)):
        return ("%g" % value)
    return str(value)


def _salary(compensation):
    if not isinstance(compensation, dict):
        return None

    summary = _clean(compensation.get("compensationTierSummary") or compensation.get("scrapeableCompensationSalarySummary"))
    components = compensation.get("summaryComponents") or []
    if not components:
        return summary

    parts = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        currency = _clean(comp.get("currencyCode"))
        interval = _clean(comp.get("interval"))
        min_value = comp.get("minValue")
        max_value = comp.get("maxValue")
        label = _clean(comp.get("label") or comp.get("name"))

        if min_value is None and max_value is None:
            continue
        if min_value is not None and max_value is not None and min_value != max_value:
            amount = _format_amount(min_value) + "-" + _format_amount(max_value)
        else:
            amount = _format_amount(min_value if min_value is not None else max_value)

        text = " ".join(part for part in [currency, amount] if part)
        if interval:
            text = text + " / " + interval
        if label:
            text = label + ": " + text
        parts.append(text)

    return "; ".join(parts) if parts else summary


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
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue

        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        description = _clean(job.get("descriptionPlain")) or _html_to_text(job.get("descriptionHtml"))
        location = _location(job)

        if not title or not url or not description or not location:
            continue

        item = {
            "url": url,
            "title": title,
            "company": "Kraken",
            "location": location,
            "description": description,
            "source": SITE,
        }

        optional = {
            "employment_type": _clean(job.get("employmentType")),
            "workplace_type": _clean(job.get("workplaceType")) or ("Remote" if job.get("isRemote") is True else None),
            "department": _clean(job.get("department") or job.get("team")),
            "posted_at": _clean(job.get("publishedAt")),
            "external_id": _clean(job.get("id")),
            "apply_url": _clean(job.get("applyUrl")),
            "salary": _salary(job.get("compensation")),
        }
        for key, value in optional.items():
            if value is not None:
                item[key] = value
        for key, value in _structured_salary(job.get("compensation")).items():
            item[key] = value

        yield item
