
import json

SITE = "https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true"

def _text_response(resp):
    if isinstance(resp, str):
        return resp
    if isinstance(resp, bytes):
        return resp.decode("utf-8", "replace")
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)

def _first_salary_component(comp):
    if not isinstance(comp, dict):
        return None
    for component in comp.get("summaryComponents") or []:
        if not isinstance(component, dict):
            continue
        if component.get("compensationType") == "Salary":
            mn = component.get("minValue")
            mx = component.get("maxValue")
            cur = component.get("currencyCode")
            interval = component.get("interval")
            if mn is not None or mx is not None:
                bits = []
                if mn is not None:
                    bits.append(str(mn))
                if mx is not None:
                    bits.append(str(mx))
                text = " - ".join(bits)
                if cur:
                    text = cur + " " + text
                if interval:
                    text = text + " / " + interval
                return text
    summary = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
    return summary or None

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

def _clean(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value

def crawl(ctx):
    data = json.loads(_text_response(ctx.fetch(SITE)))
    for job in data.get("jobs") or []:
        if job.get("isListed") is False:
            continue

        description = _clean(job.get("descriptionPlain")) or _clean(job.get("descriptionHtml"))
        title = _clean(job.get("title"))
        url = _clean(job.get("jobUrl"))
        location = _clean(job.get("location"))
        if not (description and title and url and location):
            continue

        out = {
            "url": url,
            "title": title,
            "company": "OpenAI",
            "location": location,
            "description": description,
            "source": SITE,
        }

        mappings = [
            ("employmentType", "employment_type"),
            ("workplaceType", "workplace_type"),
            ("department", "department"),
            ("publishedAt", "posted_at"),
            ("id", "external_id"),
            ("applyUrl", "apply_url"),
        ]
        for src, dst in mappings:
            val = _clean(job.get(src))
            if val is not None:
                out[dst] = val

        salary = _first_salary_component(job.get("compensation"))
        if salary:
            out["salary"] = salary
        for key, value in _structured_salary(job.get("compensation")).items():
            out[key] = value

        yield out
